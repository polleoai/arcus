import importlib.metadata

import pytest

from arcus.cli import main as cli_main
from arcus.cli.main import build_parser, main


def test_parser_accepts_basic_url() -> None:
    parser = build_parser()
    args = parser.parse_args(["https://youtube.com/watch?v=abc"])
    assert args.command == "extract"
    assert args.input == "https://youtube.com/watch?v=abc"


def test_parser_check_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["--check"])
    assert args.command == "check"


def test_parser_probe_requires_input() -> None:
    parser = build_parser()
    args = parser.parse_args(["--probe", "https://example.com"])
    assert args.command == "probe"
    assert args.input == "https://example.com"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--version"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "arcus" in captured.out.lower()


def test_help_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    captured = capsys.readouterr()
    assert excinfo.value.code == 0
    assert "arcus" in captured.out.lower()


def test_no_args_returns_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])
    assert exit_code == 2


def test_version_matches_installed_package_metadata(capsys):
    """cmd_version prints the version reported by package metadata, so the
    printed version can never drift from the published wheel's version."""
    expected = importlib.metadata.version("arcus-cli")
    rc = cli_main.cmd_version()
    out = capsys.readouterr().out
    assert rc == 0
    assert expected in out


def test_resolve_version_falls_back_when_metadata_missing(monkeypatch):
    """When package metadata is unavailable (e.g. running from a raw checkout
    that was never installed), resolve falls back to the module constant."""
    def _raise(_name):
        raise importlib.metadata.PackageNotFoundError("arcus-cli")
    monkeypatch.setattr(importlib.metadata, "version", _raise)
    assert cli_main._resolve_version() == cli_main._FALLBACK_VERSION
