import pytest

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
