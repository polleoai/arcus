import json
import sys
from pathlib import Path

from arcus.provider_runtime.log import EventLogger, STAGES, now_iso


def test_now_iso_returns_utc_iso8601() -> None:
    s = now_iso()
    assert s.endswith("Z") or "+" in s
    assert "T" in s


def test_logger_appends_ndjson_lines(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path, json_log_stderr=False)
    logger.emit({"ts": "2026-05-17T00:00:00Z", "source_id": "abc", "status": "started"})
    logger.emit({"ts": "2026-05-17T00:00:01Z", "source_id": "abc", "status": "success"})

    log_file = tmp_path / ".log" / "extract-log.ndjson"
    assert log_file.exists()

    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["status"] == "started"
    assert json.loads(lines[1])["status"] == "success"


def test_logger_creates_log_dir_lazily(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path, json_log_stderr=False)
    assert not (tmp_path / ".log").exists()

    logger.emit({"ts": "now", "event": "test"})
    assert (tmp_path / ".log" / "extract-log.ndjson").exists()


def test_logger_mirrors_to_stderr_when_enabled(
    tmp_path: Path, capsys: "pytest.CaptureFixture[str]"
) -> None:
    logger = EventLogger(tmp_path, json_log_stderr=True)
    logger.emit({"ts": "now", "status": "success"})

    captured = capsys.readouterr()
    assert '"status": "success"' in captured.err or '"status":"success"' in captured.err
    assert captured.out == ""


def test_stage_emits_uniform_discriminator_key(tmp_path: Path, capsys) -> None:
    """stage() stamps a single `event` discriminator and an ISO `ts`, and
    mirrors to stderr when json_log_stderr=True."""
    logger = EventLogger(tmp_path, json_log_stderr=True)
    logger.stage("fetching", kind="pdf", source_id="/x.pdf")

    err = capsys.readouterr().err.strip()
    payload = json.loads(err)
    assert payload["event"] == "fetching"
    assert payload["kind"] == "pdf"
    assert payload["source_id"] == "/x.pdf"
    assert "ts" in payload
    assert "status" not in payload


def test_stage_rejects_unknown_stage(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path)
    try:
        logger.stage("not-a-real-stage")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_stages_constant_lists_the_contract(tmp_path: Path) -> None:
    assert STAGES == (
        "started", "detected", "fetching",
        "extracting", "cache_hit", "success", "failed",
    )
