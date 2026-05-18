import json
from pathlib import Path

from arcus.provider_runtime.types import ExtractionResult, Segment, SourceMetadata
from arcus.provider_runtime.writer import (
    cache_hit_exists,
    write_failure_stub,
    write_success,
)


def make_success_result() -> ExtractionResult:
    return ExtractionResult(
        status="success",
        kind="youtube",
        extractor_detail={"caption_lang": "en", "caption_source": "uploader"},
        metadata=SourceMetadata(
            source="https://youtube.com/watch?v=abc12345678",
            source_id="abc12345678",
            title="Sample Title",
            slug="sample-title",
            author="Sample Channel",
            duration_ms=60_000,
            posted="2025-01-15",
            language="en",
        ),
        text="Hello there. This is the body.",
        segments=[
            Segment(start_ms=0, end_ms=1000, text="Hello there."),
            Segment(start_ms=1100, end_ms=2000, text="This is the body."),
        ],
        extracted_at="2026-05-17T00:00:00+00:00",
    )


def test_write_success_produces_md_with_frontmatter_and_body(tmp_path: Path) -> None:
    write_success(tmp_path, "sample-title", make_success_result())

    md = (tmp_path / "sample-title.md").read_text(encoding="utf-8")
    assert md.startswith("---\n")
    assert "source: https://youtube.com/watch?v=abc12345678" in md
    assert "source_id: abc12345678" in md
    assert "title: Sample Title" in md
    assert "kind: youtube" in md
    assert "status: success" in md
    assert "# Sample Title" in md
    assert "Hello there." in md


def test_write_success_produces_json_sidecar(tmp_path: Path) -> None:
    write_success(tmp_path, "sample-title", make_success_result())

    j = json.loads((tmp_path / "sample-title.json").read_text(encoding="utf-8"))
    assert j["status"] == "success"
    assert j["kind"] == "youtube"
    assert len(j["segments"]) == 2
    assert j["metadata"]["source_id"] == "abc12345678"


def test_write_failure_stub_preserves_url(tmp_path: Path) -> None:
    write_failure_stub(
        tmp_path,
        slug="sample-title",
        source="https://youtube.com/watch?v=abc12345678",
        source_id="abc12345678",
        kind="youtube",
        title="Sample Title",
        exit_code=30,
        extractor_attempted=["youtube-captions", "notebooklm"],
        error="captions absent; nlm timed out",
    )

    md = (tmp_path / "sample-title.md").read_text(encoding="utf-8")
    assert "status: failed" in md
    assert "exit_code: 30" in md
    assert "extractor_attempted:" in md
    assert "captions absent" in md
    assert "https://youtube.com/watch?v=abc12345678" in md
    assert "rework" in md.lower()


def test_cache_hit_only_for_success_status(tmp_path: Path) -> None:
    assert cache_hit_exists(tmp_path, "sample-title") is False

    write_success(tmp_path, "sample-title", make_success_result())
    assert cache_hit_exists(tmp_path, "sample-title") is True

    # Overwrite with a failure stub — cache hit must flip to False.
    write_failure_stub(
        tmp_path,
        slug="sample-title",
        source="x",
        source_id="abc",
        kind="youtube",
        title=None,
        exit_code=30,
        extractor_attempted=[],
        error="x",
    )
    assert cache_hit_exists(tmp_path, "sample-title") is False
