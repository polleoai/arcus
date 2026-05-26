"""Docling-primary backend across the pdf/docs/image providers.

Most tests mock `docling_extract.convert` (and are marked `docling` to
opt out of the global docling-off fixture) so they don't need torch/models. One
integration test runs real Docling on a fixture, skipped if the extra is absent.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers._shared import docling_extract
from arcus.provider_runtime.providers.docs.docs import DocsProvider
from arcus.provider_runtime.providers.image.image import ImageProvider
from arcus.provider_runtime.providers.pdf.pdf import PdfProvider

_MD = "# Quarterly Report\n\n| Region | Sales |\n| --- | --- |\n| West | 42 |"


def _ctx(tmp_path):
    return ExtractionContext(out_dir=tmp_path, work_dir=tmp_path)


def _result(markdown=_MD, locators=None):
    from arcus.provider_runtime.types import Segment
    locators = [{"segment": 0, "page": 1}] if locators is None else locators
    segments = [Segment(start_ms=0, end_ms=0, text=markdown)] if locators else []
    return docling_extract.DoclingResult(markdown=markdown, segments=segments, locators=locators)


# ── unit: helper ────────────────────────────────────────────────────


def test_convert_none_when_docling_unavailable(monkeypatch):
    monkeypatch.setattr(docling_extract, "docling_available", lambda: False)
    assert docling_extract.convert("/whatever.pdf") is None


def test_clean_markdown_strips_image_placeholders():
    """Docling's standalone `<!-- image -->` placeholder lines are removed and the
    leftover blank runs collapsed; real content (incl. tables) is preserved."""
    raw = "<!-- image -->\n\n## Heading\n\n<!-- image -->\n\n| a | b |\n| - | - |\n"
    cleaned = docling_extract._clean_markdown(raw)
    assert "<!-- image -->" not in cleaned
    assert "## Heading" in cleaned
    assert "| a | b |" in cleaned
    assert "\n\n\n" not in cleaned  # no triple-blank runs left behind


def test_title_skips_docling_image_comment():
    """Docling emits `<!-- image -->` placeholders; the title must skip them and
    use the first real heading/line instead."""
    md = "<!-- image -->\n\n## .claude/ folder, fully mapped\n\nbody"
    res = docling_extract.to_extraction_result("image", "/x.png", "x", _result(markdown=md))
    assert res.metadata.title == ".claude/ folder, fully mapped"


def test_to_extraction_result_shape():
    res = docling_extract.to_extraction_result("pdf", "/x.pdf", "x", _result())
    assert res.status == "success"
    assert res.kind == "pdf"
    assert res.text == _MD
    assert res.extractor_detail == {
        "extractor": "docling",
        "structured": True,
        "locators": [{"segment": 0, "page": 1}],   # page provenance carried through
    }
    assert len(res.segments) == 1
    assert res.metadata.title == "Quarterly Report"   # heading → title (no '#')


# ── providers route to Docling when it's available (mocked) ─────────


@pytest.mark.docling
@pytest.mark.parametrize("kind,suffix,make_provider", [
    ("pdf", ".pdf", PdfProvider),
    ("docs", ".docx", DocsProvider),
    ("image", ".png", ImageProvider),
])
def test_provider_uses_docling_when_available(kind, suffix, make_provider, tmp_path):
    f = tmp_path / f"sample{suffix}"
    f.write_bytes(b"fake bytes - content irrelevant, docling is mocked")
    prov = make_provider()
    with patch.object(docling_extract, "convert", return_value=_result()):
        res = prov.extract(prov.matches(str(f)), _ctx(tmp_path))
    assert res.status == "success"
    assert res.kind == kind
    assert res.extractor_detail["extractor"] == "docling"
    assert res.extractor_detail["structured"] is True
    assert res.extractor_detail["locators"] == [{"segment": 0, "page": 1}]
    assert "| Region | Sales |" in res.text


@pytest.mark.docling
def test_provider_falls_back_when_docling_returns_none(tmp_path):
    """When Docling yields nothing, the provider uses its lightweight extractor."""
    f = tmp_path / "doc.docx"
    f.write_bytes(b"PK\x03\x04 fake")
    prov = DocsProvider()
    with patch.object(docling_extract, "convert", return_value=None), \
         patch("arcus.provider_runtime.providers._shared.file_extract.extract_text",
               return_value={"title": "Fallback", "authors": "", "text": "plain body",
                             "tier": "pandoc", "units": [], "unit_key": None}):
        res = prov.extract(prov.matches(str(f)), _ctx(tmp_path))
    assert res.status == "success"
    assert res.extractor_detail["extractor"] != "docling"   # used the fallback engine


# ── real Docling integration (skipped unless the extra is installed) ─


@pytest.mark.docling
def test_real_docling_on_pdf_fixture(tmp_path):
    if not docling_extract.docling_available():
        pytest.skip("docling extra not installed")
    fixture = Path(__file__).parent / "pdf" / "fixtures" / "small.pdf"
    res = PdfProvider().extract(PdfProvider().matches(str(fixture)), _ctx(tmp_path))
    assert res.status == "success"
    assert res.kind == "pdf"
    assert res.extractor_detail["extractor"] == "docling"
    assert res.extractor_detail["structured"] is True
    assert res.text.strip()
    # Page provenance: real PDF → at least one page locator + matching segment.
    locators = res.extractor_detail["locators"]
    assert locators and locators[0]["page"] == 1
    assert len(res.segments) == len(locators)
