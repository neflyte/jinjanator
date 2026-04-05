"""JSON-RPC server over a UNIX domain socket."""

from __future__ import annotations

import io
import os
import socket

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonrpyc

from .cli import _run_render, get_hook_callers


if TYPE_CHECKING:
    import jinjanator_plugins

    from jinjanator_plugins import PluginHookCallers

_KNOWN_OPTIONS: frozenset[str] = frozenset(
    {
        "format",
        "format-option",
        "import-env",
        "undefined",
        "quiet",
        "customize",
        "filters",
        "tests",
        "output-file",
    }
)


class JinjanatorRPC:
    def __init__(self) -> None:
        self._plugin_hook_callers: PluginHookCallers = get_hook_callers()
        self._available_formats: dict[str, type[jinjanator_plugins.Format]] = {}
        for plugin_formats in self._plugin_hook_callers.plugin_formats():
            self._available_formats |= plugin_formats

    def jinjanate(
        self,
        template: str,
        data: str,
        options: dict[str, Any],
    ) -> str | dict[str, Any]:
        msg: str
        # Validate options keys
        unknown: set[str] = set(options) - _KNOWN_OPTIONS
        if unknown:
            msg = f"Unknown options: {sorted(unknown)}"
            raise ValueError(msg)

        # Require absolute template path
        template_path: Path = Path(template)
        if not template_path.is_absolute():
            msg = f"Template path must be absolute: {template!r}"
            raise ValueError(msg)

        # Resolve data: "-<content>" → StringIO, otherwise absolute file path
        input_data: StringIO | io.BufferedReader
        if data.startswith("-"):
            input_data = StringIO(data[1:])
        else:
            data_path: Path = Path(data)
            if not data_path.is_absolute():
                msg = f"Data path must be absolute: {data!r}"
                raise ValueError(msg)
            input_data = data_path.open("rb")

        # Map options dict → _run_render params
        format_name: str = options.get("format", "?")
        format_options: list[str] | None = options.get("format-option")
        import_env: str | None = options.get("import-env")
        undefined: bool = bool(options.get("undefined", False))
        customize_file: str | None = options.get("customize")
        filters: list[str] = options.get("filters", [])
        tests_list: list[str] = options.get("tests", [])
        output_file_str: str | None = options.get("output-file")
        output_file: Path | None = Path(output_file_str) if output_file_str else None

        # Auto-detect format from file extension if not provided
        if format_name == "?":
            if not data.startswith("-"):
                suffix: str = Path(data).suffix
                for k, v in self._available_formats.items():
                    if hasattr(v, "suffixes") and v.suffixes and suffix in v.suffixes:
                        format_name = k
                        break
            if format_name == "?":
                format_name = "env"

        result: str = _run_render(
            cwd=template_path.parent,
            environ={},
            template_name=str(template_path),
            input_data=input_data,  # type: ignore[arg-type] 
            format_name=format_name,
            format_options=format_options,
            import_env=import_env,
            undefined=undefined,
            customize_file=customize_file,
            filters=filters,
            tests=tests_list,
            output_file=output_file,
            plugin_hook_callers=self._plugin_hook_callers,
            available_formats=self._available_formats,
        )

        if output_file:
            return {"filename": str(output_file), "success": True}
        return result


def run_server(pipe_path: str) -> None:
    """Start a sequential JSON-RPC server on a UNIX domain socket.

    POSIX FIFOs (named pipes) are unidirectional and cannot carry both the
    request and the response. A UNIX domain socket provides the same
    filesystem-visible, named, bidirectional IPC semantics.

    Works on macOS, Linux, and Windows 10 build 17063+ (Python 3.10+ required,
    which satisfies the Python 3.9+ minimum for AF_UNIX on Windows).

    jsonrpyc stream-passing notes (verified against v1.3.1 source):
    - Constructor parameters are "stdin=" and "stdout=" (not instream/outstream).
    - jsonrpyc calls "io.open(stdin.fileno(), "rb")" and
      "io.open(stdout.fileno(), "wb")" with closefd=True, taking ownership of
      those file descriptors.  Passing the same socket fd for both would cause
      the first close to invalidate the second.  Fix: dup the fd twice and pass
      "io.open(..., closefd=False)" wrappers so that only the re-opened FileIO
      objects own their respective fds.
    - The built-in Watchdog thread busy-loops when readline() returns b"" (EOF):
      "[b""]" is truthy so it never sleeps.  Fix: "watch=False" plus a
      manual read loop that breaks on empty bytes.
    - "rpc._handle(line_str)" is the internal dispatch method; there is no
      public equivalent for server-side use.

    PowerShell clients connect using System.Net.Sockets.Socket(AddressFamily.Unix)
    with UnixDomainSocketEndPoint — identical code on Windows and Linux/macOS.
    NamedPipeClientStream works on Linux/macOS only (it uses CreateFile on Windows).
    """
    path: Path = Path(pipe_path)
    path.unlink(missing_ok=True)  # remove stale socket from a previous run

    print(f"Starting JSON-RPC server on {str(path)}...")
    server_sock: socket.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(str(path))
    server_sock.listen(1)

    rpc_obj = JinjanatorRPC()
    rpc: jsonrpyc.RPC | None = None
    try:
        conn: socket.socket
        while True:
            conn, _ = server_sock.accept()
            try:
                fd: int = conn.fileno()
                print(f"Accepted connection from {fd=}")
                # Dup the socket fd twice so each re-open inside RPC.__init__
                # gets an independent file descriptor.  closefd=False on the
                # wrappers ensures only the RPC's FileIO objects own those fds.
                stdin_wrap: io.BufferedReader = io.open(os.dup(fd), "rb", closefd=False)  # noqa: SIM115
                stdout_wrap: io.BufferedWriter = io.open(os.dup(fd), "wb", closefd=False)  # noqa: SIM115
                rpc = jsonrpyc.RPC(
                    target=rpc_obj,
                    stdin=stdin_wrap,  # type: ignore[arg-type] 
                    stdout=stdout_wrap,  # type: ignore[arg-type]
                    watch=False,  # avoid Watchdog busy-loop on EOF
                )
                if not rpc:
                    msg: str = "Failed to create instance of jsonrpyc.RPC"
                    raise RuntimeError(msg)
                # Manually drive the request/response loop until client disconnects
                while True:
                    print(f"Waiting for request from {fd=}")
                    line = rpc.stdin.readline()
                    if not line:  # empty bytes means EOF (client closed the connection)
                        break
                    line_str = line.decode("utf-8").strip()
                    if line_str:
                        print(f"Received request: {line_str=}")
                        rpc._handle(line_str)  # noqa: SLF001
            finally:
                if rpc:
                    print("Closing duplicated file descriptors")
                    rpc.stdin.close()  # closes the duplicated fd for reading
                    rpc.stdout.close()  # closes the duplicated fd for writing
                print("Closing connection")
                conn.close()
    except KeyboardInterrupt:
        pass
    finally:
        print("Closing server socket")
        server_sock.close()
        print(f"Removing socket file {str(path)}")
        path.unlink(missing_ok=True)
