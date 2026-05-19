"""PdfProvider TDD — matches() + predict_slug() + extract() with fixture PDF + mocks."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers.pdf.pdf import (
    PdfProvider,
    _input_to_slug,
)
from arcus.provider_runtime.types import EXIT_CODES, DetectionResult


FIXTURE_PDF = Path(__file__).parent / "fixtures" / "small.pdf"


def _ctx(tmp_path: Path) -> ExtractionContext:
    return ExtractionContext(out_dir=tmp_path, work_dir=tmp_path)


# ── matches() — local paths ─────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/tmp/foo.pdf",
    "/Users/bugster/Downloads/paper.pdf",
    "/var/folders/bx/something.pdf",
    "./relative.pdf",
    "paper.pdf",
])
def test_matches_local_pdf_path(path):
    p = PdfProvider()
    d = p.matches(path)
    assert d is not None
    assert d.kind == "pdf"
    assert d.source_id == path
    assert d.metadata["is_local"] is True
    assert d.metadata["ext"] == "pdf"


@pytest.mark.parametrize("path", [
    "/tmp/FOO.PDF",
    "/tmp/Paper.Pdf",
    "/tmp/file.PdF",
])
def test_matches_local_case_insensitive(path):
    assert PdfProvider().matches(path) is not None


# ── matches() — remote URLs ─────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "https://arxiv.org/pdf/2401.12345.pdf",
    "http://example.com/paper.pdf",
    "https://example.com/path/to/article.PDF",
    "https://example.com/file.pdf?download=1",
])
def test_matches_remote_pdf_by_suffix(url):
    p = PdfProvider()
    d = p.matches(url)
    assert d is not None
    assert d.metadata["is_local"] is False


def test_matches_does_not_probe_network_for_unsuffixed_urls():
    """Provider Protocol forbids network IO in matches(). Unsuffixed URLs
    fall through to HtmlProvider in factory dispatch."""
    p = PdfProvider()
    assert p.matches("https://example.com/article-without-extension") is None
    assert p.matches("https://example.com/") is None


# ── matches() — rejections ──────────────────────────────────────────


@pytest.mark.parametrize("bad", [
    "/tmp/foo.docx",
    "/tmp/foo.txt",
    "/tmp/no_extension",
    "https://example.com/page.html",
    "",
    "not a path or url",
])
def test_matches_rejects_non_pdf(bad):
    assert PdfProvider().matches(bad) is None


# ── _input_to_slug helper ───────────────────────────────────────────


@pytest.mark.parametrize("input_str,expected", [
    ("/tmp/My Paper (2024).pdf", "my-paper-2024"),
    ("/tmp/foo.pdf", "foo"),
    ("./paper.pdf", "paper"),
    ("https://arxiv.org/pdf/2401.12345.pdf", "2401-12345"),
    ("https://example.com/files/cool-paper.pdf", "cool-paper"),
    ("https://example.com/foo.PDF?download=1", "foo"),
])
def test_input_to_slug(input_str, expected):
    assert _input_to_slug(input_str) == expected


def test_input_to_slug_handles_no_stem_remote():
    """When a remote PDF URL has only a hostname, fall back to host-derived slug."""
    # No realistic example here — but the fallback path exists for robustness.
    slug = _input_to_slug("https://arxiv.org/.pdf")
    assert slug  # non-empty


# ── predict_slug ────────────────────────────────────────────────────


def test_predict_slug_local():
    p = PdfProvider()
    d = p.matches("/tmp/My Paper.pdf")
    assert p.predict_slug(d) == "my-paper"


def test_predict_slug_remote():
    p = PdfProvider()
    d = p.matches("https://arxiv.org/pdf/2401.12345.pdf")
    assert p.predict_slug(d) == "2401-12345"


def test_predict_slug_no_network():
    p = PdfProvider()
    d = p.matches("https://example.com/paper.pdf")
    with patch(
        "arcus.provider_runtime.providers.pdf._athena_file_extract.extract_text"
    ) as ext, patch(
        "urllib.request.urlretrieve"
    ) as ur:
        slug = p.predict_slug(d)
    assert slug == "paper"
    assert not ext.called
    assert not ur.called


# ── extract() local path ────────────────────────────────────────────


def test_extract_local_pdf_happy_path(tmp_path):
    p = PdfProvider()
    d = p.matches(str(FIXTURE_PDF))
    r = p.extract(d, _ctx(tmp_path))

    assert r.status == "success", f"unexpected failure: {r.error}"
    assert r.kind == "pdf"
    assert "Body text" in r.text or "arcus PdfProvider" in r.text
    assert r.metadata.source == str(FIXTURE_PDF)
    assert r.metadata.source_id == str(FIXTURE_PDF)
    # Title falls back from PDF metadata ("Test Paper") if pdfinfo is on PATH,
    # else from filename stem ("small") via make_slug-derived fallback.
    assert r.metadata.title
    assert r.metadata.slug == "small"
    assert r.extractor_detail.get("extractor")
    assert r.segments == []


def test_extract_local_pdf_uses_authors_when_present(tmp_path):
    """pdfinfo provides Author metadata when available; PdfProvider passes it
    through as SourceMetadata.author. Skips silently if pdfinfo not installed."""
    p = PdfProvider()
    d = p.matches(str(FIXTURE_PDF))
    r = p.extract(d, _ctx(tmp_path))
    # The fixture has author 'PdfProvider Suite' in metadata; pdfinfo may or
    # may not be on PATH on the test host. Both outcomes are acceptable.
    if r.metadata.author:
        assert "PdfProvider" in r.metadata.author


# ── extract() remote path ───────────────────────────────────────────


def test_extract_remote_pdf_downloads_then_extracts(tmp_path):
    """Mock urlretrieve to copy the fixture into work_dir, then extract."""
    p = PdfProvider()
    url = "https://example.com/paper.pdf"
    d = p.matches(url)

    def fake_urlretrieve(remote_url, dest_path):
        assert remote_url == url
        shutil.copy(FIXTURE_PDF, dest_path)
        return dest_path, {"Content-Type": "application/pdf"}

    with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve), \
         patch.object(p, "_head_content_type", return_value="application/pdf"):
        r = p.extract(d, _ctx(tmp_path))

    assert r.status == "success", f"unexpected failure: {r.error}"
    assert r.metadata.source == url  # Reflects the remote URL, not the tmp path
    assert r.metadata.source_id == url
    assert r.metadata.slug == "paper"
    assert r.text


def test_extract_remote_pdf_rejects_non_pdf_content_type(tmp_path):
    p = PdfProvider()
    url = "https://example.com/paper.pdf"
    d = p.matches(url)

    with patch.object(p, "_head_content_type", return_value="text/html"):
        r = p.extract(d, _ctx(tmp_path))

    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["EXTRACTORS_EXHAUSTED"]
    assert "content-type" in r.error.lower()


def test_extract_remote_pdf_accepts_when_head_unavailable(tmp_path):
    """When HEAD doesn't return a Content-Type (None), trust the .pdf suffix
    and proceed. Real servers vary in HEAD support."""
    p = PdfProvider()
    url = "https://example.com/paper.pdf"
    d = p.matches(url)

    def fake_urlretrieve(remote_url, dest_path):
        shutil.copy(FIXTURE_PDF, dest_path)
        return dest_path, {}

    with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve), \
         patch.object(p, "_head_content_type", return_value=None):
        r = p.extract(d, _ctx(tmp_path))

    assert r.status == "success"


# ── extract() failure paths ─────────────────────────────────────────


def test_extract_returns_failure_on_missing_local_file(tmp_path):
    p = PdfProvider()
    d = p.matches("/tmp/this-does-not-exist-abc123.pdf")
    r = p.extract(d, _ctx(tmp_path))
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]
    assert "not found" in r.error.lower() or "no such" in r.error.lower()


def test_extract_returns_failure_on_empty_extracted_text(tmp_path):
    p = PdfProvider()
    d = p.matches(str(FIXTURE_PDF))
    # Force file_extract.extract_text to return the empty triple
    with patch(
        "arcus.provider_runtime.providers.pdf._athena_file_extract.extract_text",
        return_value={"title": "", "authors": "", "text": ""},
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["EXTRACTORS_EXHAUSTED"]
    assert "no text" in r.error.lower()


def test_extract_remote_pdf_handles_download_failure(tmp_path):
    p = PdfProvider()
    d = p.matches("https://example.com/paper.pdf")

    with patch("urllib.request.urlretrieve", side_effect=OSError("network down")), \
         patch.object(p, "_head_content_type", return_value="application/pdf"):
        r = p.extract(d, _ctx(tmp_path))

    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]
    assert "download" in r.error.lower() or "network" in r.error.lower()


# ── Detection roundtrip ─────────────────────────────────────────────


def test_detection_is_dataclass(tmp_path):
    d = PdfProvider().matches("/tmp/foo.pdf")
    assert isinstance(d, DetectionResult)
