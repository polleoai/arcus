"""TextProvider: local Markdown / plain-text passthrough.

Markdown is already arcus's canonical output form, so extraction is a
near-passthrough: read the file, derive a title from the first heading or
line, emit it verbatim as the body. Plain `.txt` is treated the same (it is
its own Markdown). Local files only — remote http(s) URLs are handled by the
HTML provider.
"""

from __future__ import annotations

import re
from pathlib import Path

from arcus.provider_runtime.log import now_iso
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.slug import make_slug
from arcus.provider_runtime.types import (
    EXIT_CODES,
    DetectionResult,
    ExtractionResult,
    SourceMetadata,
)

_TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".text")
_HTTP = re.compile(r"^https?://", re.IGNORECASE)
_HEADING = re.compile(r"^#+\s*")


def _title_from(body: str, fallback: str) -> str:
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        return _HEADING.sub("", line).strip()[:80] or fallback
    return fallback


class TextProvider:
    """Markdown / plain-text passthrough for local files."""

    kind = "text"

    def matches(self, raw_input: str) -> DetectionResult | None:
        if not isinstance(raw_input, str) or not raw_input:
            return None
        if _HTTP.match(raw_input):
            return None
        if not raw_input.lower().endswith(_TEXT_SUFFIXES):
            return None
        return DetectionResult(kind="text", source_id=raw_input, raw=raw_input, metadata={})

    def predict_slug(self, detection: DetectionResult) -> str:
        return make_slug(Path(detection.raw).stem) or "text"

    def extract(self, detection: DetectionResult, context: ExtractionContext) -> ExtractionResult:
        path = Path(detection.raw)
        slug = self.predict_slug(detection)
        if not path.exists():
            return self._failure(detection, slug, EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                                 f"file not found: {detection.raw}")
        context.emit_progress("extracting")
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return self._failure(detection, slug, EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                                 f"read failed: {e}")
        if not body.strip():
            return self._failure(detection, slug, EXIT_CODES["EXTRACTORS_EXHAUSTED"],
                                 "file is empty")
        return ExtractionResult(
            status="success",
            kind="text",
            extractor_detail={"extractor": "passthrough", "structured": True},
            metadata=SourceMetadata(
                source=detection.raw, source_id=detection.raw,
                title=_title_from(body, path.stem), slug=slug,
            ),
            text=body,
            segments=[],
            extracted_at=now_iso(),
        )

    def _failure(self, detection, slug, exit_code, error) -> ExtractionResult:
        return ExtractionResult(
            status="failed", kind="text", extractor_detail={},
            metadata=SourceMetadata(source=detection.raw, source_id=detection.source_id,
                                    title="", slug=slug),
            text="", segments=[], extracted_at=now_iso(),
            error=error, exit_code=exit_code,
        )
