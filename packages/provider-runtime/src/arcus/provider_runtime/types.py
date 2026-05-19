"""Shared type definitions for arcus provider-runtime."""

from dataclasses import dataclass, field
from typing import Any, Literal


EXIT_CODES: dict[str, int] = {
    "SUCCESS": 0,
    "INVALID_ARGS": 2,
    "PROVIDER_PRIMARY_FAILED": 10,
    "PROVIDER_FORCED_NO_MATCH": 11,
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


@dataclass(frozen=True)
class DetectionResult:
    """Output of Provider.matches() — confirms this provider handles the input."""

    kind: str
    source_id: str
    raw: str
    metadata: dict[str, Any] = field(default_factory=dict)


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
