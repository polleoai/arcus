"""Shared Docling backend: high-fidelity file → Markdown for pdf/docs/image.

Docling (the optional `[docling]` extra) is the **primary** engine when it is
installed: it does layout analysis + table structure (TableFormer) and emits
clean, structured Markdown (incl. real Markdown tables) across pdf / docx / pptx
/ xlsx / images. The pdf/docs/image providers fall back to their lightweight
extractors (pymupdf4llm / pandoc / rapidocr+rapidtable) when Docling is absent or
a conversion fails — so the base install stays light and fast.

Apple-Silicon note: Docling's layout model (RT-DETR) tries to allocate a float64
tensor, which Metal/MPS doesn't support, so we pin the accelerator to CPU.

Provenance: `convert()` also groups the document's text items by Docling page
number, producing per-page `segments` plus parallel `locators`
(`{"segment": i, "page": n}`) — the same shape the lightweight fallback emits, so
a consumer can map output back to source pages regardless of which engine ran.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
from collections import OrderedDict
from dataclasses import dataclass, field

# A DocumentConverter loads models on construction (seconds), so build one and
# reuse it. Module-level + lazy so importing this module stays cheap. Keyed by
# whether PDF picture images are generated (keeping figures renders pages).
_converters: dict[bool, object] = {}
# FigureOptions for the convert() calls in this thread/task (see keeping_figures).
_figure_opts: contextvars.ContextVar = contextvars.ContextVar("arcus_docling_figures", default=None)


@contextlib.contextmanager
def keeping_figures(figures):
    """Within the block, convert() also generates PDF picture images (when
    `figures` is an enabled FigureOptions). Kept out of convert()'s signature
    so the one-argument contract stays stable for callers and stubs."""
    token = _figure_opts.set(figures)
    try:
        yield
    finally:
        _figure_opts.reset(token)

_HEADING = re.compile(r"^#+\s*")
# Docling emits standalone `<!-- image -->` (and similar) HTML-comment placeholders
# where it found a figure it couldn't render to text — noise in the output body.
_HTML_COMMENT_LINE = re.compile(r"^\s*<!--.*-->\s*$")
_BLANK_RUN = re.compile(r"\n{3,}")


def _clean_markdown(markdown: str) -> str:
    """Drop standalone HTML-comment placeholder lines and collapse the blank runs
    their removal leaves behind."""
    kept = [ln for ln in markdown.splitlines() if not _HTML_COMMENT_LINE.match(ln)]
    return _BLANK_RUN.sub("\n\n", "\n".join(kept)).strip()


@dataclass(frozen=True)
class DoclingResult:
    """A successful Docling conversion: clean Markdown body + page provenance.

    `segments`/`locators` mirror the lightweight extractors' shape — one segment
    per source page, with a parallel `{"segment": i, "page": n}` locator. Both are
    empty when the document carries no page provenance (the body is still set).
    """

    markdown: str
    segments: list = field(default_factory=list)
    locators: list = field(default_factory=list)
    # With figures requested: RawFigure list in document order, and the
    # markdown carries one figure_token(i) placeholder line per figure.
    figures: list = field(default_factory=list)


def docling_available() -> bool:
    """True when the `[docling]` extra is importable."""
    try:
        import docling  # noqa: F401

        return True
    except ImportError:
        return False


def _build_converter(pictures: bool = False):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
    )
    from docling.document_converter import (
        DocumentConverter,
        ImageFormatOption,
        PdfFormatOption,
    )

    # Force CPU for the model-driven pipeline (PDF + image) to dodge the MPS
    # float64 bug on Apple Silicon. Office formats parse structurally and use
    # Docling's defaults.
    opts = PdfPipelineOptions()
    opts.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU)
    pdf_opts = opts
    if pictures:
        # Keep figures: render each detected picture (at 2x, ~144 dpi).
        pdf_opts = opts.model_copy()
        pdf_opts.generate_picture_images = True
        pdf_opts.images_scale = 2.0
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
        }
    )


def _pictures(doc, opts) -> list:
    """RawFigures for the document's pictures, in markdown (body) order.

    Image bytes are only produced for figures that can still be kept (big
    enough, under the cap); the rest carry no data and are reported by
    place_figures as too_small / max_figures."""
    import io

    from docling_core.types.doc import PictureItem

    from arcus.provider_runtime.providers._shared.figures import RawFigure, eligible

    out = []
    kept = 0
    for item, _level in doc.iterate_items():
        if not isinstance(item, PictureItem):
            continue
        prov = item.prov[0] if item.prov else None
        w = float(prov.bbox.width) if prov else 0.0
        h = float(prov.bbox.height) if prov else 0.0
        img = None
        if eligible(w, h, kept, opts):
            kept += 1
            try:
                img = item.get_image(doc)
            except Exception:
                img = None
        data = b""
        if img is not None:
            buf = io.BytesIO()
            img.save(buf, "PNG")
            data = buf.getvalue()
        try:
            caption = item.caption_text(doc) or ""
        except Exception:
            caption = ""
        out.append(RawFigure(
            data=data,
            page=prov.page_no if prov else None,
            width_pt=w,
            height_pt=h,
            caption=caption.strip(),
        ))
    return out


def _number_placeholders(markdown: str, n: int) -> str:
    """Turn the i-th un-numbered placeholder into figure_token(i)."""
    from arcus.provider_runtime.providers._shared.figures import PLACEHOLDER, figure_token

    parts = markdown.split(PLACEHOLDER)
    out = [parts[0]]
    for i, part in enumerate(parts[1:]):
        out.append(figure_token(i) if i < n else "")
        out.append(part)
    return "".join(out)


def _page_segments(doc) -> tuple[list, list]:
    """Group the document's text items by page → (segments, locators).

    Best-effort: items without page provenance or text are skipped. Tables are
    already rendered into the Markdown body, so they don't add segments here.
    Returns ([], []) when no page provenance is available.
    """
    from arcus.provider_runtime.types import Segment

    by_page: OrderedDict[int, list[str]] = OrderedDict()
    for item, _level in doc.iterate_items():
        prov = getattr(item, "prov", None) or []
        page = prov[0].page_no if prov else None
        text = getattr(item, "text", None)
        if page is None or not text:
            continue
        by_page.setdefault(page, []).append(text)

    segments: list = []
    locators: list = []
    for i, (page, chunks) in enumerate(by_page.items()):
        segments.append(Segment(start_ms=0, end_ms=0, text="\n".join(chunks)))
        locators.append({"segment": i, "page": page})
    return segments, locators


def convert(filepath: str) -> DoclingResult | None:
    """Convert `filepath` to a DoclingResult (Markdown + page provenance).

    Inside `keeping_figures(opts)` (PDF provider), picture images are also
    generated and returned as `DoclingResult.figures`, with numbered
    placeholders in the markdown for `figures.place_figures`.

    Returns **None** when Docling is unavailable or the conversion fails/yields
    nothing — the signal for the caller to fall back to its lightweight
    extractor. Never raises.
    """
    if not docling_available():
        return None
    figures = _figure_opts.get()
    pictures = bool(figures is not None and figures.enabled)
    try:
        if pictures not in _converters:
            _converters[pictures] = _build_converter(pictures)
        doc = _converters[pictures].convert(filepath).document
        raws: list = []
        if pictures:
            from arcus.provider_runtime.providers._shared.figures import PLACEHOLDER

            raws = _pictures(doc, figures)
            md = doc.export_to_markdown(image_placeholder=PLACEHOLDER) or ""
            md = _number_placeholders(md, len(raws))
        else:
            md = doc.export_to_markdown() or ""
        markdown = _clean_markdown(md)
        if not markdown:
            return None
        segments, locators = _page_segments(doc)
        return DoclingResult(markdown=markdown, segments=segments, locators=locators,
                             figures=raws)
    except Exception:
        return None


def _title_from_markdown(markdown: str, fallback: str) -> str:
    for raw in markdown.splitlines():
        line = raw.strip()
        # skip blanks, table rows, and Docling's `<!-- image -->` placeholders
        if not line or line.startswith("|") or line.startswith("<!--"):
            continue
        return _HEADING.sub("", line).strip().strip("*").strip()[:80] or fallback
    return fallback


def to_extraction_result(kind: str, source: str, slug: str, result: DoclingResult,
                         figures=None):
    """Build a success ExtractionResult from a DoclingResult.

    `structured=True`; `locators` carries the per-page provenance from
    `result.locators`. Title is the first heading/line of the Markdown. With
    `figures` (FigureOptions) the figures are bounded, saved as assets and
    referenced in place (extractor_detail "figures" / "figures_skipped").
    """
    from arcus.provider_runtime.log import now_iso
    from arcus.provider_runtime.providers._shared.figures import place_figures
    from arcus.provider_runtime.types import ExtractionResult, SourceMetadata

    detail = {
        "extractor": "docling",
        "structured": True,
        "locators": result.locators,
    }
    markdown, assets = result.markdown, []
    if figures is not None and figures.enabled:
        markdown, assets, kept, skipped = place_figures(
            result.markdown, result.figures, slug, figures)
        detail["figures"] = kept
        detail["figures_skipped"] = skipped

    return ExtractionResult(
        status="success",
        kind=kind,
        extractor_detail=detail,
        metadata=SourceMetadata(
            source=source,
            source_id=source,
            title=_title_from_markdown(markdown, slug),
            slug=slug,
        ),
        text=markdown,
        segments=result.segments,
        extracted_at=now_iso(),
        assets=assets,
    )
