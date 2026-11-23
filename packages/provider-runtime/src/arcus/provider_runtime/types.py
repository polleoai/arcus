"""Shared type definitions for arcus provider-runtime."""

from dataclasses import dataclass, field
from typing import Any, Literal


EXIT_CODES: dict[str, int] = {
    "SUCCESS": 0,
    "INVALID_ARGS": 2,
    "PROVIDER_PRIMARY_FAILED": 10,
    "PROVIDER_FORCED_NO_MATCH": 11,
    "URL_REFUSED": 12,
    "PROVIDER_FALLBACK_FAILED": 20,
    "TOOL_NOT_AUTHENTICATED": 21,
    "EXTRACTORS_EXHAUSTED": 30,
    "VIDEO_RESTRICTED": 40,
    "RATE_LIMITED": 41,
}


@dataclass(frozen=True)
class Segment:
    """One timed text segment from an extraction (e.g., a caption cue)."""

    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class SourceMetadata:
    """Provider-independent metadata about an extracted source."""

    source: str
    source_id: str
    title: str
    slug: str
    author: str | None = None
    duration_ms: int | None = None
    posted: str | None = None
    language: str | None = None
    # Author-supplied description blurb (e.g. YouTube's "...more" block,
    # PDF /Subject metadata, HTML <meta name="description">). Optional —
    # absent when the source has none. Distinct from the extracted body
    # text: this is what the creator chose to surface, not what was
    # transcribed/scraped.
    description: str | None = None


@dataclass(frozen=True)
class DetectionResult:
    """Output of Provider.matches() — confirms this provider handles the input."""

    kind: str
    source_id: str
    raw: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FigureOptions:
    """Per-document bounds for keeping figures (PDF provider).

    Figures smaller than `min_side_pt` (PDF points, 1/72 in) on either side
    are treated as decorative and skipped. Past `max_figures` or
    `max_total_bytes` the document still extracts; the remaining figures are
    skipped and listed in extractor_detail["figures_skipped"].
    """

    enabled: bool = True
    max_figures: int = 50
    max_total_bytes: int = 50_000_000
    min_side_pt: float = 40.0
    # An embedded image whose header claims more pixels than this is never
    # decoded (decompression-bomb guard); it is skipped as "too_large".
    max_image_megapixels: float = 50.0


@dataclass(frozen=True)
class PdfLimits:
    """Per-document resource budget, applied before any PDF tier runs.

    A page whose area at `render_dpi` exceeds `max_page_megapixels` is never
    rendered or parsed by a tier; pages past `max_pages` are not processed.
    Both are listed in extractor_detail["pages_skipped"]. A document with no
    page inside the budget fails with a clear error.
    """

    max_pages: int = 2000
    max_page_megapixels: float = 50.0
    render_dpi: float = 300.0  # the highest DPI a tier may render at (OCR)


@dataclass(frozen=True)
class ExtractionLimits:
    """Explicit caps on extracted text, so nothing is ever cut silently.

    `max_text_chars` caps the text of the docs tiers (docx, xlsx, pptx, epub);
    None disables it. When a cap cuts, extractor_detail carries
    ``truncated=True``, ``truncated_reason="max_text_chars"`` and
    ``original_chars``. PDF text is never capped here: its size is bounded by
    PdfLimits.max_pages.
    """

    max_text_chars: int | None = 50_000


@dataclass(frozen=True)
class Asset:
    """A binary file that belongs to an extraction (e.g. a figure PNG).

    `path` is relative to the output directory (e.g.
    `<slug>.figures/figure-01-p3.png`); the writer stores `data` there.
    """

    path: str
    data: bytes = field(repr=False)


@dataclass
class ExtractionResult:
    """End-to-end result of a single-source extraction.

    arcus is a pure download/extraction layer per
    feedback-arcus-pure-download-layer — one input URL/path, one result.
    No composite/multi-source/recursive shape; consumers iterate at their
    layer if they need to handle multiple sources.
    """

    status: Literal["success", "failed"]
    kind: str
    extractor_detail: dict[str, Any]
    metadata: SourceMetadata
    text: str
    segments: list[Segment]
    extracted_at: str
    error: str | None = None
    exit_code: int | None = None
    # Files written next to `<slug>.md` (figure PNGs). Not part of the JSON
    # payload; each is described in extractor_detail (e.g. "figures").
    assets: list[Asset] = field(default_factory=list)
