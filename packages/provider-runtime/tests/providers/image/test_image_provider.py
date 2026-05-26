"""ImageProvider TDD — matches() + predict_slug() + extract() with OCR mocked.

Recognition (`image._recognize`) is monkeypatched so these tests don't require the
`[image]` extra (RapidOCR) to be installed. A separate skip-if-missing
integration test exercises the real OCR path when RapidOCR is present.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers.image import image as image_mod
from arcus.provider_runtime.providers.image.image import ImageProvider, OcrUnavailableError
from arcus.provider_runtime.types import EXIT_CODES


def _ctx(tmp_path, stages=None):
    kw = {}
    if stages is not None:
        kw["emit_progress"] = stages.append
    return ExtractionContext(out_dir=tmp_path, work_dir=tmp_path, **kw)


# ── matches() ───────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/tmp/scan.png",
    "/photos/diagram.jpg",
    "/x/a.jpeg",
    "/x/a.gif",
    "/x/a.webp",
    "/x/UPPER.PNG",
])
def test_matches_local_images(path):
    d = ImageProvider().matches(path)
    assert d is not None
    assert d.kind == "image"
    assert d.metadata["is_local"] is True


@pytest.mark.parametrize("url", [
    "https://example.com/diagram.png",
    "http://example.com/a/b/photo.JPG",
    "https://example.com/x.webp?v=2",
])
def test_matches_remote_images(url):
    d = ImageProvider().matches(url)
    assert d is not None
    assert d.metadata["is_local"] is False


@pytest.mark.parametrize("bad", [
    "/tmp/note.md",
    "/tmp/doc.pdf",
    "/tmp/no_ext",
    "https://example.com/article",
    "",
])
def test_matches_rejects_non_images(bad):
    assert ImageProvider().matches(bad) is None


def test_predict_slug():
    p = ImageProvider()
    assert p.predict_slug(p.matches("/tmp/My Scan.png")) == "my-scan"
    assert p.predict_slug(p.matches("https://example.com/files/diagram.png")) == "diagram"


# ── extract() — success (OCR mocked) ────────────────────────────────


def test_extract_local_success(tmp_path):
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG fake")
    with patch.object(image_mod, "_recognize",
                      return_value=("# Invoice\n\nTotal: $42", False, "rapidocr")):
        res = ImageProvider().extract(ImageProvider().matches(str(img)), _ctx(tmp_path))
    assert res.status == "success"
    assert res.kind == "image"
    assert "Total: $42" in res.text
    assert res.metadata.title == "Invoice"          # first heading/line → title
    assert res.extractor_detail["extractor"] == "rapidocr"
    assert res.extractor_detail["structured"] is False
    assert res.segments == []


def test_extract_table_image_emits_markdown_table(tmp_path):
    """When the table tier recovers a grid, the body is a Markdown table and
    structured=True (the fix for tables flattening into a cell list)."""
    img = tmp_path / "table.png"
    img.write_bytes(b"\x89PNG fake")
    table_md = "**Cap**\n\n| A | B |\n| --- | --- |\n| 1 | 2 |"
    with patch.object(image_mod, "_recognize",
                      return_value=(table_md, True, "rapidocr+rapidtable")):
        res = ImageProvider().extract(ImageProvider().matches(str(img)), _ctx(tmp_path))
    assert res.status == "success"
    assert res.extractor_detail["structured"] is True
    assert res.extractor_detail["extractor"] == "rapidocr+rapidtable"
    assert "| A | B |" in res.text
    assert res.metadata.title == "Cap"   # caption (stripped of **) becomes title


def test_extract_local_emits_only_extracting(tmp_path):
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG fake")
    stages: list[str] = []
    with patch.object(image_mod, "_recognize", return_value=("some text", False, "rapidocr")):
        ImageProvider().extract(ImageProvider().matches(str(img)), _ctx(tmp_path, stages))
    assert stages == ["extracting"]


def test_extract_remote_downloads_then_ocrs(tmp_path):
    url = "https://example.com/diagram.png"
    stages: list[str] = []

    def fake_urlretrieve(remote_url, dest_path):
        Path(dest_path).write_bytes(b"\x89PNG fake")
        return dest_path, {}

    with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve), \
         patch.object(image_mod, "_recognize", return_value=("remote text", False, "rapidocr")):
        res = ImageProvider().extract(ImageProvider().matches(url), _ctx(tmp_path, stages))
    assert res.status == "success"
    assert res.metadata.source == url
    assert "remote text" in res.text
    assert stages == ["fetching", "extracting"]


# ── extract() — failures ────────────────────────────────────────────


def test_extract_missing_local_file_fails(tmp_path):
    res = ImageProvider().extract(
        ImageProvider().matches(str(tmp_path / "nope.png")), _ctx(tmp_path)
    )
    assert res.status == "failed"
    assert res.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]


def test_extract_ocr_unavailable_fails_with_hint(tmp_path):
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG fake")
    with patch.object(
        image_mod, "_recognize",
        side_effect=OcrUnavailableError("the [image] extra is not installed"),
    ):
        res = ImageProvider().extract(ImageProvider().matches(str(img)), _ctx(tmp_path))
    assert res.status == "failed"
    assert res.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]
    assert "image" in res.error.lower()  # mentions the [image] extra


def test_extract_empty_ocr_text_fails(tmp_path):
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG fake")
    with patch.object(image_mod, "_recognize", return_value=("   \n  ", False, "rapidocr")):
        res = ImageProvider().extract(ImageProvider().matches(str(img)), _ctx(tmp_path))
    assert res.status == "failed"
    assert res.exit_code == EXIT_CODES["EXTRACTORS_EXHAUSTED"]


# ── real OCR integration (skipped unless toolchain present) ─────────


def test_real_ocr_roundtrip(tmp_path):
    """Real RapidOCR roundtrip — pure-pip, runs wherever the [image] extra is
    installed (no system binary). Skips only if rapidocr/Pillow are absent."""
    pytest.importorskip("rapidocr_onnxruntime")
    pytest.importorskip("PIL")

    from PIL import Image, ImageDraw

    img_path = tmp_path / "hello.png"
    im = Image.new("RGB", (360, 90), "white")
    ImageDraw.Draw(im).text((12, 34), "HELLO ARCUS 123", fill="black")
    im.save(img_path)

    res = ImageProvider().extract(ImageProvider().matches(str(img_path)), _ctx(tmp_path))
    assert res.status == "success"
    assert "ARCUS" in res.text.upper()


# ── HTML→Markdown table converter (unit) ────────────────────────────


def test_html_table_to_markdown_builds_grid():
    html = ('<table><tr><td colspan="2">Title</td></tr>'
            '<tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>')
    md = image_mod._html_table_to_markdown(html)
    assert "**Title**" in md
    assert "| A | B |" in md
    assert "| --- | --- |" in md
    assert "| 1 | 2 |" in md


def test_html_table_to_markdown_rejects_non_grid():
    # single column → not a real table → None (caller falls back to plain text)
    assert image_mod._html_table_to_markdown(
        "<table><tr><td>a</td></tr><tr><td>b</td></tr></table>") is None
    # fewer than 2 rows → None
    assert image_mod._html_table_to_markdown(
        "<table><tr><td>a</td><td>b</td></tr></table>") is None
