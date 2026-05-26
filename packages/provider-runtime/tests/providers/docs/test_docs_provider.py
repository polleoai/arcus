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
    # xlsx/pptx now carry discrete-unit segments (sheet/slide); the
    # single-sheet/single-slide fixtures yield exactly one segment.
    # docx/epub have no stable unit and stay segment-free.
    if ext in ("xlsx", "pptx"):
        assert len(r.segments) >= 1
    else:
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


# ── file_extract: tier + units helpers (R4/R5) ──────────────────────


import zipfile

from arcus.provider_runtime.providers._shared import file_extract


def _write_minimal_pptx(path: Path, n_slides: int = 2) -> None:
    """Craft a minimal pptx zip with N slide XMLs each carrying one <a:t>."""
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(1, n_slides + 1):
            zf.writestr(
                f"ppt/slides/slide{i}.xml",
                f'<?xml version="1.0"?>'
                f'<p:sld xmlns:p="p" xmlns:a="a">'
                f"<a:t>slide {i} text</a:t></p:sld>",
            )


def _write_minimal_xlsx(path: Path) -> None:
    """Craft a minimal xlsx zip: workbook with two named sheets + sheet XMLs."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main" xmlns:r="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            '<sheet name="Revenue" sheetId="1" r:id="rId1"/>'
            '<sheet name="Costs" sheetId="2" r:id="rId2"/>'
            "</sheets></workbook>",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheetData>'
            '<row><c t="inlineStr"><is><t>rev cell</t></is></c></row>'
            "</sheetData></worksheet>",
        )
        zf.writestr(
            "xl/worksheets/sheet2.xml",
            '<?xml version="1.0"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheetData>'
            '<row><c t="inlineStr"><is><t>cost cell</t></is></c></row>'
            "</sheetData></worksheet>",
        )


def test_pptx_units_helper(tmp_path):
    f = tmp_path / "deck.pptx"
    _write_minimal_pptx(f, n_slides=2)
    units = file_extract._pptx_units(str(f))
    assert units == [
        {"slide": 1, "text": "slide 1 text"},
        {"slide": 2, "text": "slide 2 text"},
    ]


def test_extract_pptx_returns_slide_units(tmp_path, monkeypatch):
    f = tmp_path / "deck.pptx"
    _write_minimal_pptx(f, n_slides=2)
    # Force the stdlib (zipfile) fallback tier by disabling pandoc.
    monkeypatch.setattr(file_extract, "_pandoc_to_markdown", lambda *a, **k: "")
    out = file_extract._extract_pptx(str(f))
    assert out["unit_key"] == "slide"
    assert out["tier"] == "zipfile"
    assert out["units"] == [
        {"slide": 1, "text": "slide 1 text"},
        {"slide": 2, "text": "slide 2 text"},
    ]
    assert out["text"]  # body text preserved


def test_xlsx_units_helper(tmp_path):
    f = tmp_path / "book.xlsx"
    _write_minimal_xlsx(f)
    units = file_extract._xlsx_units(str(f))
    assert units == [
        {"sheet": "Revenue", "text": "rev cell"},
        {"sheet": "Costs", "text": "cost cell"},
    ]


def test_xlsx_sheet_names_helper(tmp_path):
    f = tmp_path / "book.xlsx"
    _write_minimal_xlsx(f)
    assert file_extract._xlsx_sheet_names(str(f)) == ["Revenue", "Costs"]


def test_extract_xlsx_returns_sheet_units(tmp_path, monkeypatch):
    f = tmp_path / "book.xlsx"
    _write_minimal_xlsx(f)
    monkeypatch.setattr(file_extract, "_pandoc_to_markdown", lambda *a, **k: "")
    out = file_extract._extract_xlsx(str(f))
    assert out["unit_key"] == "sheet"
    assert out["tier"] == "zipfile"
    assert out["units"] == [
        {"sheet": "Revenue", "text": "rev cell"},
        {"sheet": "Costs", "text": "cost cell"},
    ]


def test_extract_pptx_tier_pandoc_when_pandoc_runs(tmp_path, monkeypatch):
    f = tmp_path / "deck.pptx"
    _write_minimal_pptx(f, n_slides=1)
    monkeypatch.setattr(file_extract, "_pandoc_to_markdown", lambda *a, **k: "# Slide 1")
    out = file_extract._extract_pptx(str(f))
    assert out["tier"] == "pandoc"
    assert out["unit_key"] == "slide"  # units still come from stdlib helper


def test_extract_docx_epub_no_units(tmp_path, monkeypatch):
    monkeypatch.setattr(file_extract, "_pandoc_to_markdown", lambda *a, **k: "# Doc body")
    docx = tmp_path / "d.docx"
    docx.write_bytes(b"x")  # body via pandoc stub, title lookup tolerates non-zip
    out = file_extract._extract_docx(str(docx))
    assert out["units"] == []
    assert out["unit_key"] is None
    assert out["tier"] == "pandoc"


# ── DocsProvider: segments + locators + structured (R4/R5) ──────────


def test_pptx_attaches_slide_locators(monkeypatch, tmp_path):
    f = tmp_path / "deck.pptx"
    f.write_bytes(b"PK\x03\x04 fake")
    monkeypatch.setattr(
        "arcus.provider_runtime.providers._shared.file_extract.extract_text",
        lambda fp, ext: {
            "title": "Deck", "authors": "", "tier": "pandoc",
            "text": "slide 1 text\n\nslide 2 text",
            "units": [
                {"slide": 1, "text": "slide 1 text"},
                {"slide": 2, "text": "slide 2 text"},
            ],
            "unit_key": "slide",
        },
    )
    res = DocsProvider().extract(
        DocsProvider().matches(str(f)),
        ExtractionContext(out_dir=tmp_path, work_dir=tmp_path),
    )
    assert res.status == "success"
    assert res.extractor_detail["structured"] is True
    assert [s.text for s in res.segments] == ["slide 1 text", "slide 2 text"]
    assert res.extractor_detail["locators"] == [
        {"segment": 0, "slide": 1},
        {"segment": 1, "slide": 2},
    ]


def test_xlsx_attaches_sheet_locators(monkeypatch, tmp_path):
    f = tmp_path / "book.xlsx"
    f.write_bytes(b"PK\x03\x04 fake")
    monkeypatch.setattr(
        "arcus.provider_runtime.providers._shared.file_extract.extract_text",
        lambda fp, ext: {
            "title": "Book", "authors": "", "tier": "pandoc",
            "text": "rev cell\n\ncost cell",
            "units": [
                {"sheet": "Revenue", "text": "rev cell"},
                {"sheet": "Costs", "text": "cost cell"},
            ],
            "unit_key": "sheet",
        },
    )
    res = DocsProvider().extract(
        DocsProvider().matches(str(f)),
        ExtractionContext(out_dir=tmp_path, work_dir=tmp_path),
    )
    assert res.status == "success"
    assert res.extractor_detail["structured"] is True
    assert [s.text for s in res.segments] == ["rev cell", "cost cell"]
    assert res.extractor_detail["locators"] == [
        {"segment": 0, "sheet": "Revenue"},
        {"segment": 1, "sheet": "Costs"},
    ]


def test_docs_not_structured_when_fallback_tier(monkeypatch, tmp_path):
    f = tmp_path / "d.docx"
    f.write_bytes(b"PK\x03\x04 fake")
    monkeypatch.setattr(
        "arcus.provider_runtime.providers._shared.file_extract.extract_text",
        lambda fp, ext: {
            "title": "D", "authors": "", "tier": "zipfile",
            "text": KNOWN_BODY, "units": [], "unit_key": None,
        },
    )
    res = DocsProvider().extract(
        DocsProvider().matches(str(f)),
        ExtractionContext(out_dir=tmp_path, work_dir=tmp_path),
    )
    assert res.status == "success"
    assert res.extractor_detail["structured"] is False
    assert res.segments == []
    assert res.extractor_detail["locators"] == []
