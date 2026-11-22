"""Builds small PDFs with real raster figures (+ a tiny decorative logo per
page) for the figure-extraction tests. Generated at test time — no binary
fixtures in the repo."""

from __future__ import annotations

import io
from pathlib import Path


def _chart(seed: int, w: int = 480, h: int = 320) -> bytes:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([40, 20, w - 20, h - 40], outline="black", width=2)
    for i in range(6):
        v = 40 + ((seed * 37 + i * 53) % 200)
        d.rectangle([60 + i * 65, h - 40 - v, 100 + i * 65, h - 40],
                    fill=(30 + i * 30, 90, 200 - i * 25))
    for i in range(5):
        d.line([40, 20 + i * 55, 46, 20 + i * 55], fill="black")
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _logo() -> bytes:
    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", (24, 24), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


def make_figure_pdf(path: Path, n_figures: int = 2, per_page: int = 2) -> Path:
    """`n_figures` real charts, `per_page` per page, each followed by a
    'Figure N: …' caption; every page also carries a 16pt decorative logo."""
    import pymupdf

    doc = pymupdf.open()
    pages = max(1, -(-n_figures // per_page))
    fig = 0
    for pno in range(1, pages + 1):
        pg = doc.new_page(width=612, height=792)
        pg.insert_image(pymupdf.Rect(560, 20, 576, 36), stream=_logo())
        y = 60
        pg.insert_text((72, y), f"Section {pno}. A study of things", fontsize=16)
        y += 30
        for _ in range(4):
            pg.insert_text((72, y), "This paragraph describes the experimental setup "
                                    "and the results in detail.", fontsize=10)
            y += 14
        for _ in range(per_page):
            if fig >= n_figures:
                break
            y += 10
            pg.insert_image(pymupdf.Rect(126, y, 486, y + 240), stream=_chart(fig))
            y += 250
            pg.insert_text((72, y), f"Figure {fig + 1}: Throughput of configuration "
                                    f"{chr(65 + fig)} across runs.", fontsize=10)
            y += 30
            fig += 1
    doc.save(str(path))
    return path
