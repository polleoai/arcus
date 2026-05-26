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


def test_write_success_returns_absolute_paths(tmp_path: Path) -> None:
    md, js = write_success(tmp_path, "sample-title", make_success_result())
    assert md == (tmp_path / "sample-title.md").resolve()
    assert js == (tmp_path / "sample-title.json").resolve()
    assert md.is_absolute() and js.is_absolute()


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


def _result_with(title: str, slug: str, text: str) -> ExtractionResult:
    return ExtractionResult(
        status="success",
        kind="text",
        extractor_detail={},
        metadata=SourceMetadata(
            source="/tmp/in.md", source_id="/tmp/in.md", title=title, slug=slug,
        ),
        text=text,
        segments=[],
        extracted_at="2026-05-17T00:00:00+00:00",
    )


def test_write_success_does_not_duplicate_h1_when_body_opens_with_heading(
    tmp_path: Path,
) -> None:
    """When the body already opens with its own H1, the writer must NOT
    prepend `# {title}` again — the heading must appear exactly once."""
    write_success(tmp_path, "heading", _result_with("Heading", "heading", "# Heading\n\nbody"))

    md = (tmp_path / "heading.md").read_text(encoding="utf-8")
    body = md.split("---\n", 2)[-1]  # body after frontmatter
    assert body.count("# Heading") == 1
    assert "body" in body


def test_write_success_does_not_duplicate_when_body_opens_with_h2(
    tmp_path: Path,
) -> None:
    """Docling bodies open with their own `## Heading`. The writer must NOT prepend
    a duplicate `# {title}` on top of a non-H1 heading either."""
    write_success(tmp_path, "report", _result_with("Report", "report", "## Report\n\nbody"))

    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    body = md.split("---\n", 2)[-1]
    assert body.strip().startswith("## Report")   # body's own H2, no prepended H1
    assert body.count("## Report") == 1
    assert body.count("Report") == 1               # title not echoed a second time


def test_write_success_prepends_h1_when_body_has_no_heading(tmp_path: Path) -> None:
    """When the body does NOT open with an H1, the writer prepends the title."""
    write_success(
        tmp_path, "my-title", _result_with("My Title", "my-title", "plain body no heading")
    )

    md = (tmp_path / "my-title.md").read_text(encoding="utf-8")
    body = md.split("---\n", 2)[-1]
    assert "# My Title" in body
    assert body.index("# My Title") < body.index("plain body no heading")


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
    assert cache_hit_exists(tmp_path, "sample-title", "abc12345678") is None

    write_success(tmp_path, "sample-title", make_success_result())
    assert cache_hit_exists(tmp_path, "sample-title", "abc12345678") is not None

    # Overwrite with a failure stub — cache hit must flip to False.
    write_failure_stub(
        tmp_path,
        slug="sample-title",
        source="x",
        source_id="abc12345678",
        kind="youtube",
        title=None,
        exit_code=30,
        extractor_attempted=[],
        error="x",
    )
    assert cache_hit_exists(tmp_path, "sample-title", "abc12345678") is None


def test_cache_hit_requires_source_id_match(tmp_path: Path) -> None:
    """Two videos with colliding title slugs must NOT cache-hit each other."""
    write_success(tmp_path, "sample-title", make_success_result())

    # Same slug, different source_id → cache MISS (protects against
    # false-positives from slug-only matching).
    assert cache_hit_exists(tmp_path, "sample-title", "DIFFERENT_ID") is None


def test_cache_hit_finds_disambiguated_form(tmp_path: Path) -> None:
    """When the original file has the bare slug but for a different source_id,
    the new source_id's file lives at <slug>--<8char>.md — the cache check
    must find it via the glob."""
    # First video: bare slug
    write_success(tmp_path, "sample-title", make_success_result())

    # Second video: same title, different source_id, disambiguated filename
    second = make_success_result()
    second.metadata = SourceMetadata(
        source="https://youtube.com/watch?v=zzz98765432",
        source_id="zzz98765432",
        title="Sample Title",
        slug="sample-title--zzz98765",
        author="Other Channel",
    )
    write_success(tmp_path, "sample-title--zzz98765", second)

    # Looking up the second video by predicted bare slug "sample-title"
    # should find the disambiguated form via source_id match.
    assert cache_hit_exists(tmp_path, "sample-title", "zzz98765432") is not None
    # First video still cache-hits on its bare-slug file.
    assert cache_hit_exists(tmp_path, "sample-title", "abc12345678") is not None
