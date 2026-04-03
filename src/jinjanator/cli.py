import argparse
import importlib
import os
import sys

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import (
    Any,
    TextIO,
    cast,
)

import jinja2
import jinjanator_plugins
import pluggy

from . import customize, filters, formats, version
from .context import read_context_data
from .customize import CustomizationModule


class FilePathLoader(jinja2.BaseLoader):
    def __init__(self, cwd: Path, encoding: str = "utf-8"):
        self.cwd = cwd
        self.encoding = encoding

    def get_source(
        self,
        environment: jinja2.Environment,  # noqa: ARG002
        template_name: str,
    ) -> tuple[str, str, Callable[[], bool]]:
        template_path = Path(template_name)

        if not template_path.is_absolute():
            template_path = self.cwd / template_name

        if not template_path.is_file():
            raise jinja2.TemplateNotFound(template_name)

        mtime = template_path.stat().st_mtime

        return (
            template_path.read_text(encoding=self.encoding),
            str(template_path),
            lambda: template_path.stat().st_mtime == mtime,
        )


class Jinja2TemplateRenderer:
    ENABLED_EXTENSIONS = (
        "jinja2.ext.i18n",
        "jinja2.ext.do",
        "jinja2.ext.loopcontrols",
    )

    def __init__(
        self,
        cwd: Path,
        allow_undefined: bool,  # noqa: FBT001
        j2_env_params: dict[str, Any],
        plugin_hook_callers: jinjanator_plugins.PluginHookCallers,
    ):
        j2_env_params.setdefault("keep_trailing_newline", True)
        j2_env_params.setdefault(
            "undefined",
            jinja2.Undefined if allow_undefined else jinja2.StrictUndefined,
        )
        j2_env_params.setdefault("extensions", self.ENABLED_EXTENSIONS)
        j2_env_params.setdefault("loader", FilePathLoader(cwd))

        self.env = jinja2.Environment(**j2_env_params, autoescape=False)  # noqa: S701

        for plugin_globals in plugin_hook_callers.plugin_globals():
            self.env.globals |= plugin_globals

        for plugin_filters in plugin_hook_callers.plugin_filters():
            self.env.filters |= plugin_filters

        for plugin_tests in plugin_hook_callers.plugin_tests():
            self.env.tests |= plugin_tests

        for plugin_extensions in plugin_hook_callers.plugin_extensions():
            for extension in plugin_extensions:
                self.env.add_extension(extension)

    def render(self, template_name: str, context: Mapping[str, str]) -> str:
        return self.env.get_template(template_name).render(context)


class UniqueStore(argparse.Action):
    """argparse action to restrict options to appearing only once."""

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: Any) -> None:
        self.already_seen = False
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        if self.already_seen and option_string:
            parser.error(option_string + " cannot be specified more than once.")
        setattr(namespace, self.dest, values)
        self.already_seen = True


def print_version_info(
    stream: TextIO = sys.stderr,
    *,
    plugin_identities: Iterable[str],
) -> None:
    print(
        f"{Path(sys.argv[0]).name} {version}, Jinja2 {importlib.metadata.version('jinja2')}",
        file=stream,
    )
    header_printed = False
    for plugin in plugin_identities:
        if not header_printed:
            print("Plugins:", file=stream)
            header_printed = True

        print(f"   {plugin}", file=stream)


class VersionAction(argparse.Action):
    def __init__(
        self,
        option_strings: list[str],
        plugin_identities: Iterable[str],
        dest: str = argparse.SUPPRESS,
        default: str = argparse.SUPPRESS,
        help: str = "",  # noqa: A002
    ):
        super().__init__(
            option_strings=option_strings,
            dest=dest,
            default=default,
            nargs=0,
            help=help,
        )
        self.plugin_identities = plugin_identities

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,  # noqa: ARG002
        values: str | Sequence[Any] | None,  # noqa: ARG002
        option_string: str | None = None,  # noqa: ARG002
    ) -> None:
        print_version_info(sys.stdout, plugin_identities=self.plugin_identities)
        parser.exit()


def parse_args(
    formats: Mapping[str, type[jinjanator_plugins.Format]],
    plugin_identities: Iterable[str],
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jinjanate",
        description="Command-line interface to Jinja2 for templating in shell scripts.",
        epilog="",
    )

    parser.add_argument(
        "-v",
        "--version",
        action=VersionAction,
        help="display version of this program and any installed plugins",
        plugin_identities=plugin_identities,
    )

    parser.add_argument(
        "-f",
        "--format",
        action=UniqueStore,
        default="?",
        help="Input data format",
        choices=["?", *list(formats.keys())],
    )

    parser.add_argument(
        "--format-option",
        action="append",
        metavar="option",
        dest="format_options",
        help="Options for data parser",
    )

    parser.add_argument(
        "-e",
        "--import-env",
        action=UniqueStore,
        default=None,
        metavar="VAR",
        dest="import_env",
        help=(
            "Import environment variables as `VAR` variable. Use empty string to import"
            " into the global scope"
        ),
    )

    parser.add_argument(
        "--undefined",
        action="store_true",
        dest="undefined",
        help="Allow undefined variables to be used in templates (suppress errors)",
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        dest="quiet",
        help="Suppress informational messages",
    )

    # add args for customize support
    customize.add_args(parser)

    parser.add_argument(
        "-o",
        "--output-file",
        metavar="outfile",
        dest="output_file",
        type=Path,
        help="Output to a file instead of stdout",
    )

    parser.add_argument(
        "--named-pipe",
        default=None,
        metavar="pipe-path",
        dest="named_pipe",
        help="Run as a JSON-RPC server listening on the named pipe at the given path",
    )

    parser.add_argument("template", nargs="?", default=None, help="Template file to process")

    parser.add_argument(
        "data",
        nargs="?",
        default=None,
        type=Path,
        help='Input data file name/path; "-" to use stdin',
    )

    return parser.parse_args(argv)


def get_hook_callers() -> jinjanator_plugins.PluginHookCallers:
    pm = pluggy.PluginManager("jinjanator")
    pm.add_hookspecs(jinjanator_plugins.PluginHooks)
    pm.register(filters)
    pm.register(formats)
    pm.load_setuptools_entrypoints("jinjanator")
    return cast("jinjanator_plugins.PluginHookCallers", pm.hook)


def validate_format_options(
    fmt: type[jinjanator_plugins.Format],
    options: Sequence[str] | None,
) -> jinjanator_plugins.Format:
    if options:
        if not fmt.option_names:
            raise jinjanator_plugins.FormatOptionUnknownError(fmt, options[0])

        for opt in options:
            if opt.split("=")[0] not in fmt.option_names:
                raise jinjanator_plugins.FormatOptionUnknownError(fmt, opt)

    return fmt(options)


def _run_render(  # noqa: PLR0913
    cwd: Path,
    environ: Mapping[str, str],
    template_name: str,
    input_data: TextIO | None,
    format_name: str,
    format_options: Sequence[str] | None,
    import_env: str | None,
    undefined: bool,  # noqa: FBT001
    customize_file: str | None,
    filters: Sequence[str],
    tests: Sequence[str],
    output_file: Path | None,
    plugin_hook_callers: jinjanator_plugins.PluginHookCallers,
    available_formats: dict[str, type[jinjanator_plugins.Format]],
) -> str:
    fmt = validate_format_options(available_formats[format_name], format_options)

    if format_name == "env" and input_data is None:
        context: Mapping[str, Any] = environ
    else:
        context = read_context_data(fmt, input_data, environ, import_env)

    customizations = CustomizationModule.from_file(customize_file)

    context = customizations.alter_context(context)

    renderer = Jinja2TemplateRenderer(
        cwd,
        undefined,
        j2_env_params=customizations.j2_environment_params(),
        plugin_hook_callers=plugin_hook_callers,
    )

    customize.apply(customizations, renderer.env, filters=list(filters), tests=list(tests))

    result = renderer.render(template_name, context)

    if output_file:
        with output_file.open("w") as f:
            f.write(result)
        return ""

    return result


def render_command(
    cwd: Path,
    environ: Mapping[str, str],
    stdin: TextIO | None,
    argv: Sequence[str],
) -> str:
    plugin_hook_callers = get_hook_callers()

    available_formats: dict[str, type[jinjanator_plugins.Format]] = {}

    for plugin_formats in plugin_hook_callers.plugin_formats():
        available_formats |= plugin_formats

    plugin_identities = plugin_hook_callers.plugin_identities()

    args = parse_args(available_formats, plugin_identities, argv[1:])

    if not args.quiet:
        print_version_info(plugin_identities=plugin_identities)

    if args.format == "?":
        if args.data is None or str(args.data) == "-":
            args.format = "env"
        else:
            suffix = args.data.suffix
            for k, v in available_formats.items():
                if v.suffixes and suffix in v.suffixes:
                    args.format = k
                    break
            if args.format == "?":
                print(
                    f"No format which can read '{suffix}' files available",
                    file=sys.stderr,
                )
                raise SystemExit(1)

    # We always expect a file;
    # unless the user wants 'env', and there's no input file provided.
    if args.format == "env" and args.data is None:
        """
        With the "env" format, if no dotenv filename is provided,
        we have two options: 1. The user wants to use the current
        environment 2. The user is feeding a dotenv file at stdin.
        Depending on whether we have data at stdin, we'll need to
        choose between the two.

        The problem is that in Linux, you can't reliably determine
        whether there is any data at stdin: some environments would
        open the descriptor even though they're not going to feed any
        data in.  That's why many applications would ask you to
        explicitly specify a '-' when stdin should be used.

        And this is what we're going to do here as well. The script,
        however, would give the user a hint that they should use '-'.
        """
        input_data_f = None
    else:
        input_data_f = stdin if args.data is None or str(args.data) == "-" else args.data.open()

    try:
        result = _run_render(
            cwd=cwd,
            environ=environ,
            template_name=args.template,
            input_data=input_data_f,
            format_name=args.format,
            format_options=args.format_options,
            import_env=args.import_env,
            undefined=args.undefined,
            customize_file=args.customize,
            filters=args.filters,
            tests=args.tests,
            output_file=args.output_file,
            plugin_hook_callers=plugin_hook_callers,
            available_formats=available_formats,
        )
    except jinja2.exceptions.UndefinedError as e:
        # When there's data at stdin, tell the user they should use '-'
        try:
            stdin_has_data = stdin is not None and not stdin.isatty()
            if args.format == "env" and args.data is None and stdin_has_data:
                extra_info = (
                    "\n\nIf you're trying to pipe a .env file, please run me with a '-'"
                    " as the data file name:\n$ {cmd} {argv} -".format(
                        cmd=Path(sys.argv[0]).name,
                        argv=" ".join(sys.argv[1:]),
                    )
                )
                e.args = (e.args[0] + extra_info, *e.args[1:])
        except:  # noqa: E722, S110
            # The above code is so optional that any, ANY, error, is ignored
            pass

        # Proceed
        raise

    return result


def main(args: list[str] | None = None) -> int | None:  # noqa: PLR0911
    if args is None:  # pragma: no cover
        args = sys.argv

    # Lightweight pre-parse: check for --named-pipe and whether a template was
    # supplied, without loading plugins (which parse_args requires for format choices).
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--named-pipe", dest="named_pipe", default=None)
    _pre.add_argument("-v", "--version", dest="version_flag", action="store_true", default=False)
    _pre.add_argument("template", nargs="?", default=None)
    _pre_args, _ = _pre.parse_known_args(args[1:])

    if _pre_args.named_pipe:
        if _pre_args.template is not None:
            print(
                "error: --named-pipe cannot be combined with a template argument",
                file=sys.stderr,
            )
            return 1
        from .rpc import run_server  # noqa: PLC0415

        run_server(_pre_args.named_pipe)
        return None

    if _pre_args.template is None and not _pre_args.version_flag:
        print("error: template argument is required", file=sys.stderr)
        return 1

    try:
        output = render_command(Path.cwd(), os.environ, sys.stdin, args)
    except jinjanator_plugins.FormatOptionUnknownError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except jinjanator_plugins.FormatOptionUnsupportedError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except jinjanator_plugins.FormatOptionValueError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code

        return 1

    sys.stdout.write(output)

    return None
