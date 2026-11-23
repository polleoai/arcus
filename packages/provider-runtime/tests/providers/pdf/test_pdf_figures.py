"""PDF figures are kept (0.8.0): both extraction tiers save each real figure as
a PNG next to the output, reference it in the markdown at the figure's
position, list it in extractor_detail["figures"], skip tiny decorative images,
and respect the per-document bounds — reporting whatever they skip.

Assertions check the files actually written to out_dir, not just the markdown.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from arcus.provider_runtime import Factory, register_defaults
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers.pdf.pdf import PdfProvider
from arcus.provider_runtime.registry import ProviderRegistry

from ._figure_pdf import make_figure_pdf

pytest.importorskip("pymupdf4llm")
pytest.importorskip("PIL")

def FigureOptions(**kw):  # noqa: N802 — imported lazily so collection never breaks
    from arcus.provider_runtime.types import FigureOptions as _F

    return _F(**kw)


IMG_REF = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _png_ok(path: Path) -> tuple[int, int]:
    from PIL import Image

    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    with Image.open(io.BytesIO(data)) as im:
        im.verify()
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def _run(tmp_path: Path, pdf: Path, **kw) -> tuple[int, Path, dict, str]:
    reg = ProviderRegistry()
    register_defaults(reg)
    out = tmp_path / "out"
    code = Factory(registry=reg).run(str(pdf), out_dir=out, **kw)
    mds = sorted(out.glob("*.md"))
    assert len(mds) == 1, mds
    md = mds[0]
    payload = json.loads(md.with_suffix(".json").read_text())
    return code, md, payload, md.read_text()


def _check_written(md: Path, payload: dict, body: str, n: int) -> list[dict]:
    figs = payload["extractor_detail"].get("figures", [])
    assert len(figs) == n, figs
    fig_dir = md.parent / f"{md.stem}.figures"
    written = sorted(fig_dir.glob("*.png")) if fig_dir.exists() else []
    assert len(written) == n, written
    refs = IMG_REF.findall(body)
    assert len(refs) == n, refs
    for (alt, target), fig in zip(refs, figs):
        assert target == fig["file"]
        f = md.parent / target
        assert f.exists() and f.parent == fig_dir
        w, h = _png_ok(f)
        assert (w, h) == (fig["width"], fig["height"])
        assert fig["bytes"] == f.stat().st_size
    return figs


def _caption_follows_ref(body: str, n: int) -> None:
    """Each figure reference sits at its figure: the 'Figure k' caption is the
    nearest caption to reference k (directly before or after it)."""
    lines = [ln for ln in body.splitlines() if ln.strip()]
    ref_idx = [i for i, ln in enumerate(lines) if IMG_REF.search(ln)]
    assert len(ref_idx) == n
    for k, i in enumerate(ref_idx, start=1):
        near = lines[max(0, i - 1): i + 2]
        assert any(ln.lstrip().startswith(f"Figure {k}:") for ln in near), (k, near)


# ── pymupdf4llm tier (Docling off: conftest default) ────────────────────────

def test_pymupdf_tier_keeps_two_figures_skips_decorative(tmp_path):
    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=2)
    code, md, payload, body = _run(tmp_path, pdf)
    assert code == 0
    assert payload["extractor_detail"]["extractor"] == "pymupdf4llm"
    figs = _check_written(md, payload, body, 2)
    assert [f["page"] for f in figs] == [1, 1]
    assert figs[0]["caption"].startswith("Figure 1:")
    assert figs[1]["caption"].startswith("Figure 2:")
    _caption_follows_ref(body, 2)
    skipped = payload["extractor_detail"]["figures_skipped"]
    assert [s["reason"] for s in skipped] == ["too_small"]  # the logo


def test_pymupdf_tier_survives_ocr_failure(tmp_path, monkeypatch):
    """pymupdf4llm's OCR hook can crash (e.g. an incompatible rapidocr); the
    tier must retry without OCR instead of silently dropping to pdftotext
    (which keeps no figures)."""
    import pymupdf4llm

    real = pymupdf4llm.to_markdown

    def flaky(*a, **k):
        if k.get("use_ocr", True):
            raise AttributeError("'RapidOCR' object has no attribute 'text_detector'")
        return real(*a, **k)

    monkeypatch.setattr(pymupdf4llm, "to_markdown", flaky)
    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=2)
    code, md, payload, body = _run(tmp_path, pdf)
    assert code == 0 and payload["extractor_detail"]["extractor"] == "pymupdf4llm"
    _check_written(md, payload, body, 2)


def test_pymupdf_tier_empty_text_falls_back_to_pdftotext(tmp_path, monkeypatch):
    """A text-less pymupdf4llm result (e.g. a scan without OCR) must not win
    over pdftotext."""
    import pymupdf4llm

    monkeypatch.setattr(pymupdf4llm, "to_markdown",
                        lambda *a, **k: [{"text": "", "metadata": {"page_number": 1}}])
    from arcus.provider_runtime.providers._shared import file_extract

    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=1)
    out = file_extract.extract_text(str(pdf), "pdf")
    assert out["tier"] in ("pdftotext", "")


def test_max_figures_bound_keeps_extracting_and_reports(tmp_path):
    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=3, per_page=2)
    code, md, payload, body = _run(tmp_path, pdf, figures=FigureOptions(max_figures=2))
    assert code == 0
    figs = _check_written(md, payload, body, 2)
    assert [f["caption"][:8] for f in figs] == ["Figure 1", "Figure 2"]
    reasons = [s["reason"] for s in payload["extractor_detail"]["figures_skipped"]]
    assert reasons.count("max_figures") == 1
    assert "Figure 3:" in body  # the text is still all there


def test_total_bytes_bound(tmp_path):
    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=2)
    code, md, payload, body = _run(tmp_path, pdf, figures=FigureOptions(max_total_bytes=1))
    assert code == 0
    _check_written(md, payload, body, 0)
    reasons = [s["reason"] for s in payload["extractor_detail"]["figures_skipped"]]
    assert reasons.count("max_total_bytes") == 2


def test_figures_disabled(tmp_path):
    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=2)
    code, md, payload, body = _run(tmp_path, pdf, figures=FigureOptions(enabled=False))
    assert code == 0
    assert not IMG_REF.search(body)
    assert not (md.parent / f"{md.stem}.figures").exists()
    assert payload["extractor_detail"].get("figures", []) == []


def test_reextract_replaces_stale_figures(tmp_path):
    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=2)
    _, md, _, _ = _run(tmp_path, pdf)
    stale = md.parent / f"{md.stem}.figures" / "figure-99-p9.png"
    stale.write_bytes(b"old")
    code, md, payload, body = _run(tmp_path, pdf, force=True)
    assert code == 0 and not stale.exists()
    _check_written(md, payload, body, 2)


def test_provider_result_carries_assets_for_library_callers(tmp_path):
    """Library callers using provider.extract() directly get the PNG bytes on
    the result (written by the writer when going through Factory)."""
    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=2)
    p = PdfProvider()
    d = p.matches(str(pdf))
    r = p.extract(d, ExtractionContext(out_dir=tmp_path, work_dir=tmp_path / "w"))
    assert r.status == "success"
    assert [a.path for a in r.assets] == [f["file"] for f in r.extractor_detail["figures"]]
    assert all(a.data[:8] == b"\x89PNG\r\n\x1a\n" for a in r.assets)


def test_cli_max_figures_flag(tmp_path):
    from arcus.cli.main import main

    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=2)
    out = tmp_path / "out"
    assert main([str(pdf), "--out", str(out), "--max-figures", "1"]) == 0
    md = next(out.glob("*.md"))
    assert len(list((out / f"{md.stem}.figures").glob("*.png"))) == 1


# ── Docling tier ────────────────────────────────────────────────────────────

@pytest.mark.docling
def test_docling_tier_keeps_two_figures(tmp_path):
    pytest.importorskip("docling")
    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=2)
    code, md, payload, body = _run(tmp_path, pdf)
    assert code == 0
    assert payload["extractor_detail"]["extractor"] == "docling"
    figs = _check_written(md, payload, body, 2)
    assert [f["page"] for f in figs] == [1, 1]
    assert figs[0]["caption"].startswith("Figure 1:")
    assert figs[1]["caption"].startswith("Figure 2:")
    _caption_follows_ref(body, 2)


@pytest.mark.docling
def test_docling_tier_max_figures_bound(tmp_path):
    pytest.importorskip("docling")
    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=3, per_page=2)
    code, md, payload, body = _run(tmp_path, pdf, figures=FigureOptions(max_figures=2))
    assert code == 0
    _check_written(md, payload, body, 2)
    reasons = [s["reason"] for s in payload["extractor_detail"]["figures_skipped"]]
    assert reasons.count("max_figures") == 1


# ── caption detection on real-world pymupdf4llm layout ─────────────────────

def test_caption_found_past_picture_text_block():
    """pymupdf4llm puts a 'picture text' block (text recognised inside the
    figure) between the image and its caption, and papers often write the
    caption as '**Figure 3** An owner …' (bold number, no colon)."""
    from arcus.provider_runtime.providers._shared.figures import caption_after

    lines = [
        "**Inside a run** Task 094",
        "",
        "[[arcus-figure:0]]",
        "",
        "**----- Start of picture text -----**<br>",
        "KIM-210<br>Workplace 175<br>Colleagues 19<br>",
        "**----- End of picture text -----**<br>",
        "",
        "**Figure 3** An owner clarification changes a timesheet export.",
        "",
        "## **4.3 Workplace Information Challenges**",
    ]
    assert caption_after(lines, 2) == "Figure 3 An owner clarification changes a timesheet export."


def test_caption_a_few_lines_after_the_image_token():
    """arXiv layouts: axis labels / legend text / a sub-caption sit between the
    image token and its 'Figure N:' line (more than two non-blank lines)."""
    from arcus.provider_runtime.providers._shared.figures import caption_after

    lines = ["[[arcus-figure:0]]", "", "0.5 1.0 1.5", "", "Loss", "", "Steps", "",
             "(a) left panel", "", "Figure 4: Pretrained 1.5B models' performance.", "",
             "Body text."]
    assert caption_after(lines, 0) == "Figure 4: Pretrained 1.5B models' performance."


def test_caption_search_stops_at_heading():
    from arcus.provider_runtime.providers._shared.figures import caption_after

    lines = ["[[arcus-figure:0]]", "", "## 5 Results", "", "Figure 9: next section's"]
    assert caption_after(lines, 0) == ""


def test_caption_not_taken_from_unrelated_text():
    from arcus.provider_runtime.providers._shared.figures import caption_after

    lines = ["[[arcus-figure:0]]", "", "Some paragraph.", "", "Another one.", "",
             "More text.", "", "Yet more.", "", "And more.", "", "Still more.", "",
             "Figure 9: far away caption"]
    assert caption_after(lines, 0) == ""


def test_place_figures_finds_missing_caption_in_text():
    """When a tier supplies no caption (Docling doesn't always link one), the
    caption next to the figure's placeholder in the markdown is used."""
    import io as _io

    from PIL import Image

    from arcus.provider_runtime.providers._shared.figures import (
        RawFigure,
        figure_token,
        place_figures,
    )

    buf = _io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, "PNG")
    md = f"Intro.\n\nFigure 4 Five workplace challenges.\n\n{figure_token(0)}\n\nNext section."
    raw = RawFigure(data=buf.getvalue(), page=2, width_pt=300, height_pt=200, caption="")
    out, assets, figs, skipped = place_figures(md, [raw], "doc", FigureOptions())
    assert figs[0]["caption"] == "Figure 4 Five workplace challenges."
    assert "![Figure 4 Five workplace challenges.](doc.figures/figure-01-p2.png)" in out
