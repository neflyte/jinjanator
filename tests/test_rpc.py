from pathlib import Path
from typing import Any

import pytest

import jinjanator.cli

from jinjanator.rpc import JinjanatorRPC


@pytest.fixture
def rpc() -> JinjanatorRPC:
    return JinjanatorRPC()


@pytest.fixture
def template_file(tmp_path: Path) -> Path:
    t = tmp_path / "hello.j2"
    t.write_text("Hello {{ name }}!")
    return t


def test_jinjanate_raw_yaml_data(rpc: JinjanatorRPC, template_file: Path) -> None:
    result = rpc.jinjanate(
        template=str(template_file),
        data="-name: world",
        options={"format": "yaml"},
    )
    assert result == "Hello world!"


def test_jinjanate_output_file(rpc: JinjanatorRPC, template_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    result = rpc.jinjanate(
        template=str(template_file),
        data="-name: world",
        options={"format": "yaml", "output-file": str(out)},
    )
    assert result == {"filename": str(out), "success": True}
    assert out.read_text() == "Hello world!"


def test_jinjanate_file_data(rpc: JinjanatorRPC, template_file: Path, tmp_path: Path) -> None:
    data_file = tmp_path / "data.yaml"
    data_file.write_text("name: world\n")
    result = rpc.jinjanate(
        template=str(template_file),
        data=str(data_file),
        options={},
    )
    assert result == "Hello world!"


def test_jinjanate_unknown_option(rpc: JinjanatorRPC, template_file: Path) -> None:
    with pytest.raises(ValueError, match="Unknown options"):
        rpc.jinjanate(
            template=str(template_file),
            data="-name: world",
            options={"bogus-key": "value"},
        )


def test_jinjanate_relative_template_path(rpc: JinjanatorRPC) -> None:
    with pytest.raises(ValueError, match="Template path must be absolute"):
        rpc.jinjanate(
            template="relative/path.j2",
            data="-name: world",
            options={"format": "yaml"},
        )


def test_jinjanate_relative_data_path(rpc: JinjanatorRPC, template_file: Path) -> None:
    with pytest.raises(ValueError, match="Data path must be absolute"):
        rpc.jinjanate(
            template=str(template_file),
            data="relative/data.yaml",
            options={"format": "yaml"},
        )


def test_cli_named_pipe_with_template(capsys: Any, tmp_path: Path) -> None:
    result = jinjanator.cli.main(["", "--named-pipe", str(tmp_path / "p"), "template.j2"])
    assert result == 1
    assert "--named-pipe cannot be combined" in capsys.readouterr().err


def test_cli_no_template(capsys: Any) -> None:
    result = jinjanator.cli.main([""])
    assert result == 1
    assert "template argument is required" in capsys.readouterr().err
