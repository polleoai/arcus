"""DocsProvider TDD — docx/xlsx/pptx/epub local + remote."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers.docs.docs import (
    DocsProvider,
    _input_to_slug,
)
from arcus.provider_runtime.types import EXIT_CODES, DetectionResult


FIXTURE_DIR = Path(__file__).parent / "fixtures"
KNOWN_BODY = "Body text for DocsProvider testing."


def _ctx(tmp_path: Path) -> ExtractionContext:
    return ExtractionContext(out_dir=tmp_path, work_dir=tmp_path)


# ── matches() — local paths ─────────────────────────────────────────


@pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx", "epub"])
def test_matches_local_each_extension(ext):
    p = DocsProvider()
    path = f"/tmp/foo.{ext}"
    d = p.matches(path)
    assert d is not None
    assert d.kind == "docs"
    assert d.source_id == path
    assert d.metadata["is_local"] is True
    assert d.metadata["ext"] == ext


@pytest.mark.parametrize("path", [
    "/tmp/FOO.DOCX",
    "/tmp/sheet.Xlsx",
    "/tmp/deck.PPTX",
    "/tmp/book.EPub",
])
def test_matches_case_insensitive(path):
    d = DocsProvider().matches(path)
    assert d is not None
    # Normalized ext is lowercase
    assert d.metadata["ext"] in ("docx", "xlsx", "pptx", "epub")


# ── matches() — remote URLs ─────────────────────────────────────────


@pytest.mark.parametrize("url,ext", [
    ("https://example.com/doc.docx", "docx"),
    ("http://example.com/sheet.xlsx", "xlsx"),
    ("https://example.com/path/deck.pptx", "pptx"),
    ("https://example.com/book.epub?download=1", "epub"),
])
def test_matches_remote_by_suffix(url, ext):
    d = DocsProvider().matches(url)
    assert d is not None
    assert d.metadata["is_local"] is False
    assert d.metadata["ext"] == ext


def test_matches_does_not_probe_network():
    p = DocsProvider()
    assert p.matches("https://example.com/no-extension") is None


# ── matches() — rejections ──────────────────────────────────────────


@pytest.mark.parametrize("bad", [
    "/tmp/foo.pdf",   # PdfProvider handles those
    "/tmp/foo.html",
    "/tmp/foo.txt",
    "/tmp/foo",
    "https://example.com/page.html",
    "",
    "not a path or url",
])
def test_matches_rejects_non_docs(bad):
    assert DocsProvider().matches(bad) is None


# ── _input_to_slug ──────────────────────────────────────────────────


@pytest.mark.parametrize("input_str,expected", [
    ("/tmp/My Document (2024).docx", "my-document-2024"),
    ("/tmp/foo.xlsx", "foo"),
    ("./report.pptx", "report"),
    ("https://example.com/files/quarterly.xlsx", "quarterly"),
    ("https://example.com/book.EPUB?download=1", "book"),
])
def test_input_to_slug(input_str, expected):
    assert _input_to_slug(input_str) == expected


# ── predict_slug ────────────────────────────────────────────────────


def test_predict_slug_local():
    p = DocsProvider()
    d = p.matches("/tmp/My Document.docx")
    assert p.predict_slug(d) == "my-document"


def test_predict_slug_remote():
    p = DocsProvider()
    d = p.matches("https://example.com/files/quarterly.xlsx")
    assert p.predict_slug(d) == "quarterly"


def test_predict_slug_no_network():
    p = DocsProvider()
    d = p.matches("https://example.com/foo.docx")
    with patch(
        "arcus.provider_runtime.providers._shared.file_extract.extract_text"
    ) as ext, patch("urllib.request.urlretrieve") as ur:
        slug = p.predict_slug(d)
    assert slug == "foo"
    assert not ext.called
    assert not ur.called


# ── extract() local — happy paths per format ────────────────────────


@pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx", "epub"])
def test_extract_local_happy_path(ext, tmp_path):
    fixture = FIXTURE_DIR / f"small.{ext}"
    assert fixture.exists(), f"missing fixture: {fixture}"

    p = DocsProvider()
    d = p.matches(str(fixture))
    r = p.extract(d, _ctx(tmp_path))

    assert r.status == "success", f"{ext}: unexpected failure: {r.error}"
    assert r.kind == "docs"
    assert KNOWN_BODY in r.text, f"{ext}: missing body in {r.text!r}"
    assert r.metadata.source == str(fixture)
    assert r.metadata.source_id == str(fixture)
    assert r.metadata.slug == "small"
    assert r.metadata.title  # non-empty (either file metadata or stem fallback)
    assert r.extractor_detail.get("extractor")
    assert r.extractor_detail.get("ext") == ext
    assert r.segments == []


# ── progress emission ───────────────────────────────────────────────


def test_extract_local_emits_only_extracting(tmp_path):
    local_doc = tmp_path / "doc.docx"
    local_doc.write_bytes(b"fake docx")
    p = DocsProvider()
    d = p.matches(str(local_doc))

    stages: list[str] = []
    ctx = ExtractionContext(
        out_dir=tmp_path, work_dir=tmp_path, emit_progress=stages.append
    )
    with patch(
        "arcus.provider_runtime.providers._shared.file_extract.extract_text",
        return_value={"title": "T", "authors": "", "text": KNOWN_BODY},
    ):
        r = p.extract(d, ctx)
    assert r.status == "success"
    assert stages == ["extracting"]


# ── extract() remote ────────────────────────────────────────────────


def test_extract_remote_downloads_then_extracts(tmp_path):
    p = DocsProvider()
    url = "https://example.com/quarterly.docx"
    d = p.matches(url)

    def fake_urlretrieve(remote_url, dest_path):
        assert remote_url == url
        shutil.copy(FIXTURE_DIR / "small.docx", dest_path)
        return dest_path, {"Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}

    with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        r = p.extract(d, _ctx(tmp_path))

    assert r.status == "success", f"unexpected failure: {r.error}"
    assert r.metadata.source == url
    assert r.metadata.source_id == url
    assert r.metadata.slug == "quarterly"
    assert KNOWN_BODY in r.text


# ── extract() failure paths ─────────────────────────────────────────


def test_extract_returns_failure_on_missing_local(tmp_path):
    p = DocsProvider()
    d = p.matches("/tmp/this-does-not-exist-zzz999.docx")
    r = p.extract(d, _ctx(tmp_path))
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]
    assert "not found" in r.error.lower()


def test_extract_returns_failure_on_empty_text(tmp_path):
    p = DocsProvider()
    fixture = FIXTURE_DIR / "small.docx"
    d = p.matches(str(fixture))
    with patch(
        "arcus.provider_runtime.providers._shared.file_extract.extract_text",
        return_value={"title": "", "authors": "", "text": ""},
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["EXTRACTORS_EXHAUSTED"]
    assert "no text" in r.error.lower()


def test_extract_remote_handles_download_failure(tmp_path):
    p = DocsProvider()
    d = p.matches("https://example.com/foo.docx")
    with patch("urllib.request.urlretrieve", side_effect=OSError("network down")):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]
    assert "download" in r.error.lower() or "network" in r.error.lower()


# ── Detection roundtrip ─────────────────────────────────────────────


def test_detection_is_dataclass():
    d = DocsProvider().matches("/tmp/foo.docx")
    assert isinstance(d, DetectionResult)
