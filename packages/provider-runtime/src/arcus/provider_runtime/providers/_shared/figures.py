"""Keep figures: bound, name and place extracted figure images (shared by the
Docling and pymupdf4llm PDF tiers).

Each tier hands over its markdown with one placeholder line per figure, in
document order (``figure_token(i)``), plus the raw figures in the same order.
:func:`place_figures` applies the per-document bounds, turns kept figures
into PNG :class:`Asset` files under ``<slug>.figures/`` and markdown image
references at the placeholder, removes the placeholders of skipped ones, and
reports what it kept and skipped — the document always extracts.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from arcus.provider_runtime.types import Asset, FigureOptions

_TOKEN = "[[arcus-figure:{}]]"
_TOKEN_RE = re.compile(r"^[ \t]*\[\[arcus-figure:(\d+)\]\][ \t]*$", re.MULTILINE)
_CAPTION_RE = re.compile(r"^\s*(?:\*\*|_)?\s*(fig(?:ure)?\.?\s*\d+[a-z]?\b.*)", re.IGNORECASE)
_BLANK_RUN = re.compile(r"\n{3,}")


def figure_token(i: int) -> str:
    return _TOKEN.format(i)


def strip_tokens(text: str) -> str:
    return _BLANK_RUN.sub("\n\n", _TOKEN_RE.sub("", text)).strip()


@dataclass
class RawFigure:
    """One figure as found by a tier: image bytes (any PIL-readable format),
    its page (1-indexed), on-page size in PDF points, and caption if known."""

    data: bytes
    page: int | None
    width_pt: float
    height_pt: float
    caption: str = ""


def caption_after(lines: list[str], i: int) -> str:
    """Best-effort caption: the first non-blank line after line `i` (or before
    it) that reads like 'Figure N …' / 'Fig. N …'."""
    for j in list(range(i + 1, min(len(lines), i + 4))) + list(range(i - 1, max(-1, i - 3), -1)):
        line = lines[j].strip()
        if not line:
            continue
        m = _CAPTION_RE.match(line)
        if m:
            return m.group(1).strip().strip("*_").strip()
    return ""


def _to_png(data: bytes) -> tuple[bytes, int, int] | None:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if im.mode not in ("RGB", "RGBA", "L", "LA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            out = io.BytesIO()
            im.save(out, "PNG", optimize=True)
            return out.getvalue(), im.width, im.height
    except Exception:
        return None


def _alt(caption: str, n: int) -> str:
    text = re.sub(r"\s+", " ", caption).strip() or f"Figure {n}"
    return text.replace("[", "(").replace("]", ")")[:200]


def place_figures(markdown: str, raws: list[RawFigure], slug: str,
                  opts: FigureOptions) -> tuple[str, list[Asset], list[dict], list[dict]]:
    """Returns (markdown, assets, figures, skipped).

    `figures` entries: {file, page, caption, width, height, bytes}.
    `skipped` entries: {page, reason, caption} with reason one of
    ``too_small`` / ``max_figures`` / ``max_total_bytes`` / ``unreadable``.
    """
    assets: list[Asset] = []
    figures: list[dict] = []
    skipped: list[dict] = []
    refs: dict[int, str] = {}
    total = 0
    if opts.enabled:
        for i, raw in enumerate(raws):
            reason = None
            if min(raw.width_pt, raw.height_pt) < opts.min_side_pt:
                reason = "too_small"
            elif len(figures) >= opts.max_figures:
                reason = "max_figures"
            png = None
            if reason is None:
                png = _to_png(raw.data)
                if png is None:
                    reason = "unreadable"
                elif total + len(png[0]) > opts.max_total_bytes:
                    reason = "max_total_bytes"
            if reason is not None:
                skipped.append({"page": raw.page, "reason": reason, "caption": raw.caption})
                continue
            data, w, h = png
            n = len(figures) + 1
            page = f"-p{raw.page}" if raw.page else ""
            path = f"{slug}.figures/figure-{n:02d}{page}.png"
            total += len(data)
            assets.append(Asset(path=path, data=data))
            figures.append({"file": path, "page": raw.page, "caption": raw.caption,
                            "width": w, "height": h, "bytes": len(data)})
            refs[i] = f"![{_alt(raw.caption, n)}]({path})"

    placed: set[int] = set()

    def sub(m: re.Match) -> str:
        i = int(m.group(1))
        if i in refs:
            placed.add(i)
            return refs[i]
        return ""

    out = _TOKEN_RE.sub(sub, markdown)
    # A kept figure whose placeholder isn't in the text (e.g. the text was
    # truncated) is still referenced — at the end — so no file is orphaned.
    tail = [refs[i] for i in sorted(refs) if i not in placed]
    if tail:
        out = out.rstrip() + "\n\n" + "\n\n".join(tail) + "\n"
    return _BLANK_RUN.sub("\n\n", out).strip(), assets, figures, skipped
