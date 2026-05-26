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

import re
from collections import OrderedDict
from dataclasses import dataclass, field

# A DocumentConverter loads models on construction (seconds), so build one and
# reuse it. Module-level + lazy so importing this module stays cheap.
_converter = None

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


def docling_available() -> bool:
    """True when the `[docling]` extra is importable."""
    try:
        import docling  # noqa: F401

        return True
    except ImportError:
        return False


def _build_converter():
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
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
        }
    )


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

    Returns **None** when Docling is unavailable or the conversion fails/yields
    nothing — the signal for the caller to fall back to its lightweight
    extractor. Never raises.
    """
    global _converter
    if not docling_available():
        return None
    try:
        if _converter is None:
            _converter = _build_converter()
        doc = _converter.convert(filepath).document
        markdown = _clean_markdown(doc.export_to_markdown() or "")
        if not markdown:
            return None
        segments, locators = _page_segments(doc)
        return DoclingResult(markdown=markdown, segments=segments, locators=locators)
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


def to_extraction_result(kind: str, source: str, slug: str, result: DoclingResult):
    """Build a success ExtractionResult from a DoclingResult.

    `structured=True`; `locators` carries the per-page provenance from
    `result.locators`. Title is the first heading/line of the Markdown.
    """
    from arcus.provider_runtime.log import now_iso
    from arcus.provider_runtime.types import ExtractionResult, SourceMetadata

    return ExtractionResult(
        status="success",
        kind=kind,
        extractor_detail={
            "extractor": "docling",
            "structured": True,
            "locators": result.locators,
        },
        metadata=SourceMetadata(
            source=source,
            source_id=source,
            title=_title_from_markdown(result.markdown, slug),
            slug=slug,
        ),
        text=result.markdown,
        segments=result.segments,
        extracted_at=now_iso(),
    )
