"""Shared Docling backend: high-fidelity file → Markdown for pdf/docs/image.

Docling (the optional `[docling]` extra) is the **primary** engine when it is
installed: it does layout analysis + table structure (TableFormer) and emits
clean, structured Markdown (incl. real Markdown tables) across pdf / docx / pptx
/ xlsx / images. The pdf/docs/image providers fall back to their lightweight
extractors (pymupdf4llm / pandoc / rapidocr+rapidtable) when Docling is absent or
a conversion fails — so the base install stays light and fast.

Apple-Silicon note: Docling's layout model (RT-DETR) tries to allocate a float64
tensor, which Metal/MPS doesn't support, so we pin the accelerator to CPU.

v1 emits Markdown only (`structured=True`); per-page/cell `locators` from Docling
provenance are a tracked follow-up (the lightweight fallback still emits its
page/sheet/slide locators).
"""

from __future__ import annotations

import re

# A DocumentConverter loads models on construction (seconds), so build one and
# reuse it. Module-level + lazy so importing this module stays cheap.
_converter = None

_HEADING = re.compile(r"^#+\s*")


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


def extract_markdown(filepath: str) -> str | None:
    """Convert `filepath` to Markdown via Docling.

    Returns the Markdown string, or **None** when Docling is unavailable or the
    conversion fails/yields nothing — the signal for the caller to fall back to
    its lightweight extractor. Never raises.
    """
    global _converter
    if not docling_available():
        return None
    try:
        if _converter is None:
            _converter = _build_converter()
        md = _converter.convert(filepath).document.export_to_markdown()
        return md.strip() or None
    except Exception:
        return None


def _title_from_markdown(markdown: str, fallback: str) -> str:
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line or line.startswith("|"):  # skip blanks + table rows
            continue
        return _HEADING.sub("", line).strip().strip("*").strip()[:80] or fallback
    return fallback


def to_extraction_result(kind: str, source: str, slug: str, markdown: str):
    """Build a success ExtractionResult from Docling Markdown.

    `structured=True`; `locators` is empty for now (Docling-provenance locators
    are a tracked follow-up). Title is the first heading/line of the Markdown.
    """
    from arcus.provider_runtime.log import now_iso
    from arcus.provider_runtime.types import ExtractionResult, SourceMetadata

    return ExtractionResult(
        status="success",
        kind=kind,
        extractor_detail={"extractor": "docling", "structured": True, "locators": []},
        metadata=SourceMetadata(
            source=source,
            source_id=source,
            title=_title_from_markdown(markdown, slug),
            slug=slug,
        ),
        text=markdown,
        segments=[],
        extracted_at=now_iso(),
    )
