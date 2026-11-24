"""The `nlm` CLI (notebooklm-mcp-cli) is a declared, verified dependency (#11).

The YouTube NotebookLM fallback shells out to `nlm`. It must check the
installed version against the declared range before use and fail with an
actionable message when `nlm` is missing or out of range, and it must drive
the CLI the way the declared version actually behaves: piped output is JSON,
`notebook delete` asks for confirmation, prompts must never block.

`subprocess.run` is faked with the output shapes of notebooklm-mcp-cli 0.9.4.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from arcus.provider_runtime.providers.youtube import nlm_fallback as nf


class FakeNlm:
    """Answers `nlm ...` argv the way notebooklm-mcp-cli does with a pipe."""

    def __init__(self, version="0.9.4", missing=False, authed=True, timeout_on=None):
        self.version, self.missing, self.authed, self.timeout_on = version, missing, authed, timeout_on
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        if self.missing:
            raise FileNotFoundError(2, "No such file or directory", "nlm")
        args = argv[1:]
        if self.timeout_on and args[:2] == self.timeout_on:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout") or 1)
        out, code = "", 0
        if args == ["--version"]:
            out = (f"nlm version {self.version}\n" if self.version else "nlm\n")
        elif args[:2] == ["login", "--check"]:
            code = 0 if self.authed else 1
        elif args[:2] == ["notebook", "create"]:
            out = json.dumps({"notebook_id": "nb-123", "title": args[2],
                              "url": "https://notebooklm.google.com/notebook/nb-123",
                              "message": f"Created notebook: {args[2]}"}, indent=2)
            if "--json" not in args:  # piped output is JSON either way
                pass
        elif args[:2] == ["source", "add"]:
            out = json.dumps({"source_id": "src-456", "title": "Video"}, indent=2)
        elif args[:2] == ["source", "content"]:
            out = json.dumps({"content": "Para one.\n\nPara two.", "title": "Video",
                              "source_type": "youtube", "char_count": 19}, indent=2)
        elif args[:2] == ["notebook", "delete"]:
            code = 0 if ("--confirm" in args or "-y" in args) else 1  # typer.confirm aborts
        return subprocess.CompletedProcess(argv, code, out, "")


@pytest.fixture
def fake(monkeypatch):
    def make(**kw):
        f = FakeNlm(**kw)
        monkeypatch.setattr(nf.subprocess, "run", f)
        monkeypatch.setattr(nf, "_NLM_VERIFIED", None, raising=False)
        return f
    return make


# ── declared + verified ─────────────────────────────────────────────────────

def test_declared_requirement_is_public():
    assert nf.NLM_REQUIREMENT == "notebooklm-mcp-cli>=0.9.4,<1"
    root = Path(__file__).resolve().parents[5]
    for doc in ("README.md", "docs/providers-network.md"):
        assert nf.NLM_REQUIREMENT in (root / doc).read_text(), doc


def test_missing_nlm_fails_loudly_with_the_requirement(fake):
    fake(missing=True)
    with pytest.raises(nf.NlmUnavailableError) as e:
        nf.check_auth()
    assert "notebooklm-mcp-cli>=0.9.4,<1" in str(e.value)
    assert "not found" in str(e.value)


@pytest.mark.parametrize("version", ["0.6.10", "0.9.3", "1.0.0", "2.3.1"])
def test_out_of_range_version_fails_loudly(fake, version):
    f = fake(version=version)
    with pytest.raises(nf.NlmUnavailableError) as e:
        nf.check_auth()
    assert version in str(e.value) and "notebooklm-mcp-cli>=0.9.4,<1" in str(e.value)
    assert not any(c[0][1:3] == ["login", "--check"] for c in f.calls)


def test_unreadable_version_fails_loudly(fake):
    fake(version=None)
    with pytest.raises(nf.NlmUnavailableError):
        nf.check_auth()


@pytest.mark.parametrize("version", ["0.9.4", "0.16.6"])
def test_supported_version_passes_then_checks_auth(fake, version):
    f = fake(version=version)
    nf.check_auth()
    assert [c[0][1:] for c in f.calls][:2] == [["--version"], ["login", "--check"]]


def test_unauthenticated_is_still_its_own_error(fake):
    fake(authed=False)
    with pytest.raises(nf.NlmNotAuthenticatedError):
        nf.check_auth()


def test_unavailable_is_an_nlm_error(fake):
    assert issubclass(nf.NlmUnavailableError, nf.NlmError)


# ── the CLI shape of the declared version ───────────────────────────────────

def test_create_notebook_reads_the_json_id(fake):
    fake()
    assert nf.create_notebook("arcus • Video • abc • 2026-11-23") == "nb-123"


def test_add_url_source_reads_the_json_id(fake):
    fake()
    assert nf.add_url_source("nb-123", "https://www.youtube.com/watch?v=abcdefghijk") == "src-456"


def test_source_content_returns_the_transcript_not_json(fake):
    fake()
    assert nf.get_source_content("src-456") == "Para one.\n\nPara two."


def test_delete_notebook_confirms_non_interactively(fake):
    f = fake()
    nf.delete_notebook("nb-123")
    argv = f.calls[-1][0]
    assert argv[1:3] == ["notebook", "delete"] and ("--confirm" in argv or "-y" in argv)


def test_no_call_can_block_on_a_prompt(fake):
    f = fake()
    nf.check_auth()
    nf.create_notebook("t")
    nf.add_url_source("nb-123", "https://youtu.be/x")
    nf.get_source_content("src-456")
    nf.delete_notebook("nb-123")
    assert f.calls and all(kw.get("stdin") is subprocess.DEVNULL for _, kw in f.calls)


def test_ingest_timeout_is_an_nlm_error(fake):
    fake(timeout_on=["source", "add"])
    with pytest.raises(nf.NlmError):
        nf.add_url_source("nb-123", "https://youtu.be/x", timeout_seconds=1)


def test_missing_nlm_mid_run_is_unavailable_not_oserror(fake):
    fake(missing=True)
    with pytest.raises(nf.NlmUnavailableError):
        nf.create_notebook("t")


# ── through the provider and the factory ────────────────────────────────────

def _no_captions(mocker):
    from arcus.provider_runtime.providers.youtube import youtube as yt
    from arcus.provider_runtime.providers.youtube.ytdlp_adapter import YtDlpMetadata

    yt._METADATA_CACHE.clear()
    mocker.patch.object(yt, "fetch_metadata", return_value=YtDlpMetadata(
        title="Sample", channel="Ch", duration_ms=60_000, posted=None,
        language=None, subtitle_tracks=[]))
    return yt


def _events(out: Path):
    return [json.loads(ln) for ln in (out / ".log" / "extract-log.ndjson").read_text().splitlines()]


def test_factory_reports_missing_nlm_as_a_fallback_failure(fake, mocker, tmp_path):
    from arcus.provider_runtime import Factory, register_defaults
    from arcus.provider_runtime.registry import ProviderRegistry
    from arcus.provider_runtime.types import EXIT_CODES

    yt = _no_captions(mocker)
    fake(missing=True)
    reg = ProviderRegistry()
    register_defaults(reg)
    try:
        code = Factory(registry=reg).run("https://www.youtube.com/watch?v=abcdefghijk", out_dir=tmp_path)
    finally:
        yt._METADATA_CACHE.clear()
    assert code == EXIT_CODES["PROVIDER_FALLBACK_FAILED"]
    failed = [e for e in _events(tmp_path) if e["event"] == "failed"][-1]
    assert "notebooklm-mcp-cli>=0.9.4,<1" in failed["error"]
    assert "unhandled" not in failed["error"] and "traceback" not in failed


def test_fallback_end_to_end_with_the_declared_version(fake, mocker, tmp_path):
    from arcus.provider_runtime import Factory, register_defaults
    from arcus.provider_runtime.registry import ProviderRegistry

    yt = _no_captions(mocker)
    mocker.patch.object(yt.url_guard, "check_url", return_value=None)
    mocker.patch.object(yt, "load_cached_limit", return_value=100)
    f = fake()
    reg = ProviderRegistry()
    register_defaults(reg)
    try:
        code = Factory(registry=reg).run("https://www.youtube.com/watch?v=abcdefghijk", out_dir=tmp_path)
    finally:
        yt._METADATA_CACHE.clear()
    assert code == 0
    md = next(tmp_path.glob("*.md")).read_text()
    assert "Para one." in md and "Para two." in md and "source_id" in md
    assert "{" not in md.split("---", 2)[2]  # the body is the transcript, not JSON
    deletes = [c[0] for c in f.calls if c[0][1:3] == ["notebook", "delete"]]
    assert deletes and deletes[0][3] == "nb-123"


def test_check_command_bounds_the_auth_check(monkeypatch, capsys):
    """`arcus --check` must not hang on a slow `nlm login --check`."""
    import subprocess as sp

    from arcus.cli import main as cli

    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(nf, "require_nlm", lambda: "0.9.4")
    seen = {}

    def slow(argv, **kw):
        seen.update(kw)
        raise sp.TimeoutExpired(argv, kw.get("timeout") or 0)

    monkeypatch.setattr(cli.subprocess, "run", slow)
    assert cli.cmd_check() == 0
    assert seen.get("timeout"), "nlm login --check ran without a timeout"
    assert "nlm auth" in capsys.readouterr().out
