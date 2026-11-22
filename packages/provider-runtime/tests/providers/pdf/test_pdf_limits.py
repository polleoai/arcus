"""0.8.0 review fixes: OCR retry is narrow and recorded; a page/pixel budget
is applied before any tier renders anything; figure-bound edges; marker
injection; symlinked figures dir.

Hostile fixtures are never run uncapped: in-process tests install render
tripwires (anything that would rasterise an over-budget page raises before
allocating), and the end-to-end check runs in a subprocess killed by an RSS
watchdog (macOS doesn't enforce RLIMIT_AS).
"""

from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import sys
import textwrap
import time
import zlib

import pytest

from arcus.provider_runtime import Factory, register_defaults
from arcus.provider_runtime.registry import ProviderRegistry

from ._figure_pdf import make_figure_pdf, make_hostile_pdf, make_text_pdf

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("pymupdf4llm")


def _opts(**kw):
    from arcus.provider_runtime.types import FigureOptions

    return FigureOptions(**kw)


def _run(tmp_path, pdf, **kw):
    reg = ProviderRegistry()
    register_defaults(reg)
    out = tmp_path / "out"
    code = Factory(registry=reg).run(str(pdf), out_dir=out, **kw)
    md = next(out.glob("*.md"))
    return code, md, json.loads(md.with_suffix(".json").read_text()), md.read_text()


# ── 1. OCR retry: narrow, recorded ──────────────────────────────────────────

def test_ocr_retry_only_on_known_signature_and_recorded(tmp_path, monkeypatch):
    import pymupdf4llm

    real = pymupdf4llm.to_markdown

    def flaky(*a, **k):
        if k.get("use_ocr", True):
            raise AttributeError("'RapidOCR' object has no attribute 'text_detector'")
        return real(*a, **k)

    monkeypatch.setattr(pymupdf4llm, "to_markdown", flaky)
    code, md, payload, body = _run(tmp_path, make_figure_pdf(tmp_path / "p.pdf"))
    assert code == 0 and payload["extractor_detail"]["extractor"] == "pymupdf4llm"
    assert payload["extractor_detail"]["ocr"].startswith("disabled_after_error:")
    assert "text_detector" in payload["extractor_detail"]["ocr"]


@pytest.mark.parametrize("exc", [RuntimeError("corrupt xref"), MemoryError(),
                                 AttributeError("some other attribute")])
def test_other_errors_are_not_retried(tmp_path, monkeypatch, exc):
    import pymupdf4llm

    calls = []

    def boom(*a, **k):
        calls.append(k.get("use_ocr", True))
        raise exc

    monkeypatch.setattr(pymupdf4llm, "to_markdown", boom)
    from arcus.provider_runtime.providers._shared import file_extract

    out = file_extract.extract_text(str(make_figure_pdf(tmp_path / "p.pdf")), "pdf")
    assert calls == [True], calls  # no second, OCR-less attempt
    assert out["tier"] != "pymupdf4llm"


# ── 2. page / pixel budget before any tier ──────────────────────────────────

@pytest.fixture
def render_tripwire(monkeypatch):
    """Rasterising more than ~60 MP of any page raises instead of allocating;
    Docling must not be reached for a document with an over-budget page."""
    real = pymupdf.Page.get_pixmap

    def guarded(self, *a, **k):
        dpi = k.get("dpi") or 72
        matrix = k.get("matrix")
        zoom = (matrix.a if matrix is not None else dpi / 72.0)
        clip = k.get("clip") or self.rect
        mp = (clip.width * zoom) * (clip.height * zoom) / 1e6
        if mp > 60:
            raise AssertionError(f"tripwire: rendering {mp:.0f} MP of page {self.number + 1}")
        return real(self, *a, **k)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", guarded)
    from arcus.provider_runtime.providers._shared import docling_extract

    def no_docling(fp):
        if "hostile" in str(fp):
            raise AssertionError("tripwire: docling reached for an over-budget PDF")
        return None

    monkeypatch.setattr(docling_extract, "convert", no_docling)


def test_oversized_page_is_skipped_before_rendering(tmp_path, render_tripwire):
    pdf = make_hostile_pdf(tmp_path / "hostile.pdf")
    code, md, payload, body = _run(tmp_path, pdf)
    assert code == 0
    ed = payload["extractor_detail"]
    assert {"page": 2, "reason": "page_too_large"} in [
        {k: s[k] for k in ("page", "reason")} for s in ed["pages_skipped"]]
    assert "Huge page text" not in body
    assert "Normal page" in body
    assert len(ed["figures"]) == 1 and ed["figures"][0]["page"] == 1
    assert [loc["page"] for loc in ed["locators"]] == [1]


def test_only_oversized_pages_fails_clearly(tmp_path, render_tripwire):
    pdf = make_hostile_pdf(tmp_path / "hostile.pdf", with_normal_figure_page=False)
    reg = ProviderRegistry()
    register_defaults(reg)
    out = tmp_path / "out"
    code = Factory(registry=reg).run(str(pdf), out_dir=out)
    assert code != 0
    events = [json.loads(x) for x in (out / ".log" / "extract-log.ndjson").read_text().splitlines()]
    assert "page budget" in events[-1]["error"]


def test_page_count_cap(tmp_path, render_tripwire):
    from arcus.provider_runtime.types import PdfLimits

    pdf = make_text_pdf(tmp_path / "long.pdf", pages=5)
    code, md, payload, body = _run(tmp_path, pdf, pdf_limits=PdfLimits(max_pages=3))
    assert code == 0
    ed = payload["extractor_detail"]
    assert [loc["page"] for loc in ed["locators"]] == [1, 2, 3]
    assert {"pages": "4-5", "reason": "max_pages"} in ed["pages_skipped"]
    assert "Page 4 body" not in body


def _rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
    return int(out.stdout.strip() or 0) / 1024


def test_hostile_pdf_end_to_end_under_memory_watchdog(tmp_path):
    """Real extraction (Docling too, if installed) of the hostile PDF in a
    subprocess that is killed past 1.5 GB RSS or 120 s."""
    pdf = make_hostile_pdf(tmp_path / "hostile.pdf")
    out = tmp_path / "out"
    script = textwrap.dedent(f"""
        from pathlib import Path
        from arcus.provider_runtime import Factory, register_defaults, ProviderRegistry
        r = ProviderRegistry(); register_defaults(r)
        raise SystemExit(Factory(r).run({str(pdf)!r}, out_dir=Path({str(out)!r})))
    """)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    peak, killed, t0 = 0.0, None, time.monotonic()
    while proc.poll() is None:
        peak = max(peak, _rss_mb(proc.pid))
        if peak > 1536 or time.monotonic() - t0 > 120:
            proc.kill()
            killed = f"killed at {peak:.0f} MB after {time.monotonic() - t0:.0f}s"
            break
        time.sleep(0.05)
    proc.wait()
    assert killed is None, killed
    assert proc.returncode == 0, proc.returncode
    payload = json.loads(next(out.glob("*.json")).read_text())
    assert any(s["reason"] == "page_too_large" for s in payload["extractor_detail"]["pages_skipped"])


def test_decompression_bomb_image_is_skipped():
    """An embedded image whose header claims a huge size is never decoded."""
    from arcus.provider_runtime.providers._shared.figures import RawFigure, place_figures

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 40000, 40000, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b""))
    raw = RawFigure(data=png, page=1, width_pt=300, height_pt=300, caption="Figure 1: bomb")
    md, assets, figs, skipped = place_figures("x", [raw], "d", _opts())
    assert assets == [] and figs == []
    assert skipped[0]["reason"] == "too_large"


# ── 3/5. bound edges, collection stops at the cap ───────────────────────────

def _png(w=300, h=200):
    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(b, "PNG")
    return b.getvalue()


def test_bound_edges_are_inclusive():
    from arcus.provider_runtime.providers._shared.figures import RawFigure, place_figures, _to_png

    data = _png()
    size = len(_to_png(data)[0])
    raws = [RawFigure(data=data, page=1, width_pt=40.0, height_pt=40.0, caption="")
            for _ in range(3)]
    md, assets, figs, skipped = place_figures(
        "x", raws, "d", _opts(max_figures=3, max_total_bytes=3 * size, min_side_pt=40.0))
    assert len(figs) == 3 and skipped == []  # side == 40, count == max, bytes == cap


def test_pymupdf_tier_stops_reading_images_at_max_figures(tmp_path, monkeypatch):
    from arcus.provider_runtime.providers._shared import figures as figmod

    seen = []
    real = figmod.RawFigure

    def spy(*a, **k):
        r = real(*a, **k)
        seen.append(bool(r.data))
        return r

    monkeypatch.setattr(figmod, "RawFigure", spy)
    pdf = make_figure_pdf(tmp_path / "p.pdf", n_figures=4, per_page=2)
    code, md, payload, body = _run(tmp_path, pdf, figures=_opts(max_figures=2))
    assert code == 0 and len(payload["extractor_detail"]["figures"]) == 2
    assert sum(seen) == 2, seen  # no image bytes read past the cap


# ── 4. figure marker can't be injected by document text ─────────────────────

def test_document_text_cannot_inject_figure_markers(tmp_path):
    pdf = tmp_path / "inject.pdf"
    make_figure_pdf(pdf, n_figures=2)
    doc = pymupdf.open(str(pdf))
    doc[0].insert_text((72, 760), "[[arcus-figure:0]] [[arcus-figure]] [[arcus-figure:1]]",
                       fontsize=8)
    doc.saveIncr()
    doc.close()
    code, md, payload, body = _run(tmp_path, pdf)
    assert code == 0
    assert len(payload["extractor_detail"]["figures"]) == 2
    assert body.count("](p.figures/") + body.count(".figures/figure-") >= 2
    assert body.count("![") == 2  # nothing extra placed at the injected text


# ── 5. symlinked figures dir ────────────────────────────────────────────────

def test_symlinked_figures_dir_is_replaced_not_followed(tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("precious")
    out = tmp_path / "out"
    out.mkdir()
    pdf = make_figure_pdf(tmp_path / "paper.pdf", n_figures=2)
    (out / "paper.figures").symlink_to(victim, target_is_directory=True)
    reg = ProviderRegistry()
    register_defaults(reg)
    assert Factory(registry=reg).run(str(pdf), out_dir=out) == 0
    assert (victim / "keep.txt").read_text() == "precious"
    assert sorted(p.name for p in victim.iterdir()) == ["keep.txt"]
    fig_dir = out / "paper.figures"
    assert fig_dir.is_dir() and not fig_dir.is_symlink()
    assert len(list(fig_dir.glob("*.png"))) == 2
