"""Nothing is truncated silently (#18).

PDF tiers return the whole text (size is bounded by PdfLimits.max_pages).
The docs tiers keep an explicit, documented cap — ExtractionLimits.max_text_chars —
and say so in extractor_detail when it cuts.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers._shared import docling_extract, file_extract


def _page(n: int, chars: int) -> str:
    body = f"Page {n} begins. " + ("lorem ipsum dolor sit amet " * (chars // 27 + 1))
    return body[:chars] + f" END-OF-PAGE-{n}"


# ── PDF: no cap on any tier ─────────────────────────────────────────


def test_pdf_pymupdf4llm_tier_returns_whole_text(monkeypatch, tmp_path):
    chunks = [{"text": _page(i, 50_000), "metadata": {"page_number": i}} for i in (1, 2, 3)]
    monkeypatch.setattr(file_extract, "_pdf_pages_via_pymupdf4llm", lambda fp: chunks)
    out = file_extract._extract_pdf(str(tmp_path / "x.pdf"))
    assert out["tier"] == "pymupdf4llm"
    assert len(out["text"]) > 150_000
    assert out["text"].rstrip().endswith("END-OF-PAGE-3")


def test_pdf_pdftotext_tier_returns_whole_text(monkeypatch, tmp_path):
    raw = "\n\n".join(_page(i, 40_000) for i in (1, 2, 3))
    monkeypatch.setattr(file_extract, "_pdf_pages_via_pymupdf4llm", lambda fp: None)
    monkeypatch.setattr(file_extract, "_run_tool", lambda cmd, timeout=30: raw)
    out = file_extract._extract_pdf(str(tmp_path / "x.pdf"))
    assert out["tier"] == "pdftotext"
    assert len(out["text"]) > 100_000
    assert "END-OF-PAGE-3" in out["text"]


def _long_pdf(path: Path, pages: int = 30, chars: int = 4_000) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for i in range(1, pages + 1):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(pymupdf.Rect(36, 36, 576, 756), _page(i, chars), fontsize=6)
    doc.save(str(path))
    return path


def test_real_pdf_over_100k_chars_comes_back_whole(monkeypatch, tmp_path):
    """End to end through PdfProvider on the non-docling tiers (athena's install)."""
    pytest.importorskip("pymupdf")
    from arcus.provider_runtime.providers.pdf.pdf import PdfProvider

    monkeypatch.setattr(docling_extract, "convert", lambda fp: None)
    pdf = _long_pdf(tmp_path / "long.pdf")
    p = PdfProvider()
    r = p.extract(p.matches(str(pdf)), ExtractionContext(out_dir=tmp_path, work_dir=tmp_path))
    assert r.status == "success", r.error
    assert r.extractor_detail["extractor"] != "docling"
    assert len(r.text) > 100_000
    assert "END-OF-PAGE-30" in r.text
    assert "truncated" not in r.extractor_detail


# ── docs: explicit, visible cap ─────────────────────────────────────


def test_extraction_limits_default_is_documented_50k():
    from arcus.provider_runtime import ExtractionLimits

    assert ExtractionLimits().max_text_chars == 50_000
    assert ExtractionContext(out_dir=Path("."), work_dir=Path(".")).limits == ExtractionLimits()


def _docx(path: Path, text: str) -> Path:
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
    return path


def _docs_extract(tmp_path, text, monkeypatch, limits=None):
    from arcus.provider_runtime.providers.docs.docs import DocsProvider

    # Force the flat zipfile tier so the test needs no [office] extra or pandoc.
    monkeypatch.setattr(file_extract, "_docx_via_python_docx", lambda fp: "")
    monkeypatch.setattr(file_extract, "_pandoc_to_markdown", lambda fp, fmt: "")
    path = _docx(tmp_path / "big.docx", text)
    ctx = ExtractionContext(out_dir=tmp_path, work_dir=tmp_path)
    if limits is not None:
        ctx = ExtractionContext(out_dir=tmp_path, work_dir=tmp_path, limits=limits)
    p = DocsProvider()
    return p.extract(p.matches(str(path)), ctx)


def test_docs_cut_is_flagged_with_reason_and_original_length(tmp_path, monkeypatch):
    text = _page(1, 80_000)
    r = _docs_extract(tmp_path, text, monkeypatch)
    assert r.status == "success", r.error
    assert len(r.text) == 50_000
    d = r.extractor_detail
    assert d["truncated"] is True
    assert d["truncated_reason"] == "max_text_chars"
    assert d["original_chars"] == len(text)


def test_docs_under_the_cap_is_not_flagged(tmp_path, monkeypatch):
    r = _docs_extract(tmp_path, _page(1, 1_000), monkeypatch)
    assert r.status == "success", r.error
    assert "truncated" not in r.extractor_detail


def test_docs_cap_is_configurable_and_none_disables_it(tmp_path, monkeypatch):
    from arcus.provider_runtime import ExtractionLimits

    text = _page(1, 80_000)
    r = _docs_extract(tmp_path, text, monkeypatch, ExtractionLimits(max_text_chars=10_000))
    assert len(r.text) == 10_000 and r.extractor_detail["original_chars"] == len(text)
    r = _docs_extract(tmp_path, text, monkeypatch, ExtractionLimits(max_text_chars=None))
    assert r.text.strip() == text and "truncated" not in r.extractor_detail


def test_factory_passes_extraction_limits_through(tmp_path, monkeypatch):
    import json

    from arcus.provider_runtime import ExtractionLimits, Factory, ProviderRegistry, register_defaults

    monkeypatch.setattr(file_extract, "_docx_via_python_docx", lambda fp: "")
    monkeypatch.setattr(file_extract, "_pandoc_to_markdown", lambda fp, fmt: "")
    path = _docx(tmp_path / "big.docx", _page(1, 30_000))
    reg = ProviderRegistry()
    register_defaults(reg)
    out = tmp_path / "out"
    rc = Factory(reg).run(str(path), out_dir=out, extraction_limits=ExtractionLimits(max_text_chars=5_000))
    assert rc == 0
    payload = json.loads(next(out.glob("*.json")).read_text())
    assert payload["extractor_detail"]["truncated_reason"] == "max_text_chars"
    assert len(payload["text"]) == 5_000
