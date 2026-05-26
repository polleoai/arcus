import json
from pathlib import Path

import pytest

from arcus.provider_runtime.factory import Factory
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.registry import ProviderRegistry
from arcus.provider_runtime.types import (
    EXIT_CODES,
    DetectionResult,
    ExtractionResult,
    SourceMetadata,
)


class StubProvider:
    """Test stub — matches any input starting with `kind:`."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def matches(self, raw: str) -> DetectionResult | None:
        prefix = f"{self.kind}:"
        if raw.startswith(prefix):
            return DetectionResult(
                kind=self.kind,
                source_id=raw[len(prefix):],
                raw=raw,
                metadata={},
            )
        return None

    def predict_slug(self, detection: DetectionResult) -> str:
        return detection.source_id

    def extract(self, detection: DetectionResult, context: ExtractionContext) -> ExtractionResult:
        return ExtractionResult(
            status="success",
            kind=self.kind,
            extractor_detail={},
            metadata=SourceMetadata(
                source=detection.raw,
                source_id=detection.source_id,
                title=f"Title for {detection.source_id}",
                slug=detection.source_id,
            ),
            text=f"Body for {detection.source_id}",
            segments=[],
            extracted_at="2026-05-17T00:00:00+00:00",
        )


def test_registry_first_match_wins() -> None:
    reg = ProviderRegistry()
    reg.register(StubProvider("first"))
    reg.register(StubProvider("second"))

    match = reg.detect("first:abc")
    assert match is not None
    provider, det = match
    assert provider.kind == "first"
    assert det.source_id == "abc"


def test_registry_returns_none_when_no_match() -> None:
    reg = ProviderRegistry()
    reg.register(StubProvider("x"))
    assert reg.detect("zz:abc") is None


class RaisingUrlProvider:
    """Stub whose detection.source_id is a full URL and whose extract()
    raises — exercises the never-crash unhandled-exception path."""

    kind = "raiser"

    def matches(self, raw: str) -> DetectionResult | None:
        if raw.startswith("raiser:"):
            return DetectionResult(
                kind=self.kind,
                source_id="https://example.com/a/b/thing.pdf",
                raw=raw,
                metadata={},
            )
        return None

    def predict_slug(self, detection: DetectionResult) -> str | None:
        return None  # force extraction path

    def extract(self, detection: DetectionResult, context: ExtractionContext) -> ExtractionResult:
        raise RuntimeError("boom")


def test_factory_unhandled_exception_with_url_source_writes_stub(tmp_path: Path) -> None:
    """A provider that raises with a URL source_id must NOT crash the CLI:
    the slug is sanitized so the stub `.md` write succeeds, the `failed`
    event is emitted, and PROVIDER_PRIMARY_FAILED is returned (R9)."""
    reg = ProviderRegistry()
    reg.register(RaisingUrlProvider())
    factory = Factory(registry=reg)

    # Must not raise (previously crashed with FileNotFoundError on '/').
    exit_code = factory.run("raiser:go", out_dir=tmp_path, force=False)
    assert exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]

    # A `failed` event was emitted.
    events = _read_events(tmp_path)
    assert any(e["event"] == "failed" for e in events)

    # A stub `.md` (named from the sanitized slug) exists with failed frontmatter.
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1, f"expected one stub md, found {md_files}"
    content = md_files[0].read_text(encoding="utf-8")
    assert "status: failed" in content


def test_factory_run_success_writes_outputs(tmp_path: Path) -> None:
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    factory = Factory(registry=reg)

    exit_code = factory.run("kind1:abc", out_dir=tmp_path, force=False)
    assert exit_code == EXIT_CODES["SUCCESS"]
    assert (tmp_path / "abc.md").exists()
    assert (tmp_path / "abc.json").exists()


def test_factory_forced_provider_match_success(tmp_path: Path) -> None:
    """--provider <kind> where kind matches the input → proceed with it."""
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    factory = Factory(registry=reg)

    exit_code = factory.run("kind1:abc", out_dir=tmp_path, provider="kind1")
    assert exit_code == EXIT_CODES["SUCCESS"]
    assert (tmp_path / "abc.md").exists()
    assert (tmp_path / "abc.json").exists()


def test_factory_forced_provider_no_match_exits_11(tmp_path: Path) -> None:
    """Forced provider is registered but doesn't match the input → exit 11."""
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    reg.register(StubProvider("kind2"))  # registered, but kind1:abc won't match it
    factory = Factory(registry=reg)

    exit_code = factory.run("kind1:abc", out_dir=tmp_path, provider="kind2")
    assert exit_code == EXIT_CODES["PROVIDER_FORCED_NO_MATCH"]

    events = _read_events(tmp_path)
    assert any(e["event"] == "failed" for e in events)


def test_factory_forced_provider_unknown_kind_exits_2(tmp_path: Path) -> None:
    """Forced provider kind is not registered → exit 2 (INVALID_ARGS)."""
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    factory = Factory(registry=reg)

    exit_code = factory.run("kind1:abc", out_dir=tmp_path, provider="bogus")
    assert exit_code == EXIT_CODES["INVALID_ARGS"]

    events = _read_events(tmp_path)
    failed = [e for e in events if e["event"] == "failed"]
    assert failed, "expected a failed event"
    # The error lists the valid kinds.
    assert "kind1" in failed[-1]["error"]
    assert "bogus" in failed[-1]["error"]


def test_factory_run_unsupported_input_exits_30(tmp_path: Path) -> None:
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    factory = Factory(registry=reg)

    exit_code = factory.run("vimeo://nope", out_dir=tmp_path, force=False)
    assert exit_code == EXIT_CODES["EXTRACTORS_EXHAUSTED"]


def test_register_defaults_order() -> None:
    from arcus.provider_runtime.factory import register_defaults
    reg = ProviderRegistry()
    register_defaults(reg)
    kinds = [p.kind for p in reg.all()]
    assert kinds == ["youtube", "pdf", "docs", "text", "image", "html"]


def test_dispatch_routes_to_text_for_local_md() -> None:
    from arcus.provider_runtime.factory import register_defaults
    reg = ProviderRegistry()
    register_defaults(reg)
    match = reg.detect("/tmp/notes.md")
    assert match is not None
    assert match[0].kind == "text"


@pytest.mark.parametrize("path", [
    "/tmp/scan.png",
    "https://example.com/diagram.jpg",
    "/photos/x.webp",
])
def test_dispatch_routes_to_image(path) -> None:
    from arcus.provider_runtime.factory import register_defaults
    reg = ProviderRegistry()
    register_defaults(reg)
    match = reg.detect(path)
    assert match is not None
    assert match[0].kind == "image"


def test_dispatch_routes_to_youtube_for_youtube_url() -> None:
    from arcus.provider_runtime.factory import register_defaults

    reg = ProviderRegistry()
    register_defaults(reg)
    match = reg.detect("https://www.youtube.com/watch?v=jNQXAC9IVRw")
    assert match is not None
    provider, _ = match
    assert provider.kind == "youtube"


def test_dispatch_routes_to_pdf_for_pdf_url() -> None:
    from arcus.provider_runtime.factory import register_defaults

    reg = ProviderRegistry()
    register_defaults(reg)
    match = reg.detect("https://arxiv.org/pdf/2401.12345.pdf")
    assert match is not None
    provider, _ = match
    assert provider.kind == "pdf"


def test_dispatch_routes_to_html_for_generic_url() -> None:
    from arcus.provider_runtime.factory import register_defaults

    reg = ProviderRegistry()
    register_defaults(reg)
    match = reg.detect("https://example.com/article")
    assert match is not None
    provider, _ = match
    assert provider.kind == "html"


def test_dispatch_routes_to_pdf_for_local_path() -> None:
    from arcus.provider_runtime.factory import register_defaults

    reg = ProviderRegistry()
    register_defaults(reg)
    match = reg.detect("/tmp/paper.pdf")
    assert match is not None
    provider, _ = match
    assert provider.kind == "pdf"


@pytest.mark.parametrize("path,kind", [
    ("/tmp/foo.docx", "docs"),
    ("/tmp/foo.xlsx", "docs"),
    ("/tmp/foo.pptx", "docs"),
    ("/tmp/foo.epub", "docs"),
    ("https://example.com/foo.docx", "docs"),
    ("https://example.com/sheet.xlsx", "docs"),
    ("https://example.com/deck.pptx", "docs"),
    ("https://example.com/book.epub", "docs"),
])
def test_dispatch_routes_to_docs_for_each_extension(path, kind) -> None:
    from arcus.provider_runtime.factory import register_defaults

    reg = ProviderRegistry()
    register_defaults(reg)
    match = reg.detect(path)
    assert match is not None, f"no match for {path}"
    provider, _ = match
    assert provider.kind == kind


def test_dispatch_returns_none_for_unmatched() -> None:
    from arcus.provider_runtime.factory import register_defaults

    reg = ProviderRegistry()
    register_defaults(reg)
    assert reg.detect("garbage input not a url") is None


def _read_events(out_dir):
    log = out_dir / ".log" / "extract-log.ndjson"
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_factory_emits_uniform_event_stream(tmp_path):
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    Factory(registry=reg).run("kind1:abc", out_dir=tmp_path, force=False)

    events = _read_events(tmp_path)
    assert all("event" in e for e in events)
    assert all("status" not in e for e in events)
    names = [e["event"] for e in events]
    assert names[0] == "started"
    assert "detected" in names
    assert names[-1] == "success"


def test_success_event_carries_paths_and_ids(tmp_path):
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    Factory(registry=reg).run("kind1:abc", out_dir=tmp_path, force=False)

    success = [e for e in _read_events(tmp_path) if e["event"] == "success"][0]
    assert success["kind"] == "kind1"
    assert success["source_id"] == "abc"
    assert success["slug"] == "abc"
    assert success["md_path"] == str((tmp_path / "abc.md").resolve())
    assert success["json_path"] == str((tmp_path / "abc.json").resolve())


def test_cache_hit_event_carries_paths(tmp_path):
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    f = Factory(registry=reg)
    f.run("kind1:abc", out_dir=tmp_path, force=False)
    f.run("kind1:abc", out_dir=tmp_path, force=False)  # second = cache hit

    hit = [e for e in _read_events(tmp_path) if e["event"] == "cache_hit"][0]
    assert hit["md_path"] == str((tmp_path / "abc.md").resolve())
    assert hit["source_id"] == "abc"


def test_cache_hit_event_points_to_disambiguated_file(tmp_path):
    """When the real cached file is a disambiguated form (`<slug>--<hash>.md`)
    produced by collision handling, the cache_hit event must report that
    ACTUAL file's path — not the bare `<slug>.md` (which does not exist).

    Peitho trusts cache_hit paths terminally (R7), so they must point to a
    file that exists on disk.
    """
    out_dir = tmp_path
    out_dir.mkdir(parents=True, exist_ok=True)
    # Pre-create a disambiguated cache file for source_id "abc". The bare
    # "abc.md" deliberately does NOT exist — only the disambiguated form.
    disambiguated_md = out_dir / "abc--deadbeef.md"
    disambiguated_md.write_text(
        "---\n"
        "source: kind1:abc\n"
        "source_id: abc\n"
        "title: Title for abc\n"
        "slug: abc--deadbeef\n"
        "status: success\n"
        "---\n\n# Title for abc\n\nBody\n",
        encoding="utf-8",
    )

    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))  # predict_slug returns bare "abc"
    Factory(registry=reg).run("kind1:abc", out_dir=out_dir, force=False)

    hit = [e for e in _read_events(out_dir) if e["event"] == "cache_hit"][0]
    assert hit["md_path"] == str(disambiguated_md.resolve())
    assert hit["json_path"] == str((out_dir / "abc--deadbeef.json").resolve())
    assert hit["slug"] == "abc--deadbeef"
    assert hit["source_id"] == "abc"
    assert Path(hit["md_path"]).exists()


def test_factory_cache_hit_short_circuits(tmp_path: Path) -> None:
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    factory = Factory(registry=reg)

    # First run — produces outputs
    factory.run("kind1:abc", out_dir=tmp_path, force=False)
    md_mtime = (tmp_path / "abc.md").stat().st_mtime_ns

    # Second run — cache hit, file untouched
    factory.run("kind1:abc", out_dir=tmp_path, force=False)
    assert (tmp_path / "abc.md").stat().st_mtime_ns == md_mtime

    # --force re-extracts
    factory.run("kind1:abc", out_dir=tmp_path, force=True)
    # mtime should change (or stay same if filesystem coarsens; just verify file still exists)
    assert (tmp_path / "abc.md").exists()
