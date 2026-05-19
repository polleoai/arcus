"""DocsProvider: docx, xlsx, pptx, epub — local files + remote URLs.

Single provider for all four formats — they share matches/predict_slug/extract
shape (suffix-based detection, no-network matches, local-or-remote extract,
extension-keyed dispatch to the shared file_extract module).

Naming: ``docs`` covers "general document files that aren't PDFs." PDF is its
own provider because PDF has Content-Type probing for remote and a different
primary extractor (pymupdf4llm vs python-docx/openpyxl/python-pptx/pandoc).
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from arcus.provider_runtime.log import now_iso
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.slug import make_slug
from arcus.provider_runtime.types import (
    EXIT_CODES,
    DetectionResult,
    ExtractionResult,
    SourceMetadata,
)


_HTTP_SCHEME = re.compile(r"^https?://", re.IGNORECASE)
_SUPPORTED_EXTS = ("docx", "xlsx", "pptx", "epub")
_EXT_PATTERN = re.compile(
    rf"\.({ '|'.join(_SUPPORTED_EXTS) })(\?|$)",
    re.IGNORECASE,
)
_EXTRACTOR_NAME = {
    "docx": "python-docx",
    "xlsx": "openpyxl-or-pandoc",
    "pptx": "python-pptx",
    "epub": "pandoc-or-zipfile",
}


def _is_http(s: str) -> bool:
    return bool(_HTTP_SCHEME.match(s))


def _detect_ext(path_or_url: str) -> str | None:
    """Return the lowercase ext if path_or_url ends in a supported suffix
    (ignoring query string for URLs); else None."""
    if _is_http(path_or_url):
        path = urlparse(path_or_url).path
    else:
        path = path_or_url
    m = _EXT_PATTERN.search(path)
    return m.group(1).lower() if m else None


def _input_to_slug(raw_input: str) -> str:
    """Deterministic input → slug. No IO. Local: stem; remote: URL path stem."""
    if _is_http(raw_input):
        parsed = urlparse(raw_input)
        path = parsed.path.rstrip("/")
        stem = ""
        if path:
            last = path.rsplit("/", 1)[-1]
            # Strip any of the supported extensions
            for ext in _SUPPORTED_EXTS:
                suffix = f".{ext}"
                if last.lower().endswith(suffix):
                    last = last[: -len(suffix)]
                    break
            stem = last
        slug = make_slug(stem) if stem else ""
        if slug:
            return slug
        return make_slug(parsed.netloc) or "doc"
    stem = Path(raw_input).stem
    return make_slug(stem) or "doc"


class DocsProvider:
    """Extracts docx/xlsx/pptx/epub from local paths or remote URLs."""

    kind = "docs"

    def matches(self, raw_input: str) -> DetectionResult | None:
        if not isinstance(raw_input, str) or not raw_input:
            return None
        ext = _detect_ext(raw_input)
        if ext is None:
            return None
        is_local = not _is_http(raw_input)
        return DetectionResult(
            kind="docs",
            source_id=raw_input,
            raw=raw_input,
            metadata={"is_local": is_local, "ext": ext},
        )

    def predict_slug(self, detection: DetectionResult) -> str:
        return _input_to_slug(detection.raw)

    def extract(
        self,
        detection: DetectionResult,
        context: ExtractionContext,
    ) -> ExtractionResult:
        raw = detection.raw
        slug = _input_to_slug(raw)
        ext = detection.metadata.get("ext", "")
        is_local = detection.metadata.get("is_local", True)

        if is_local:
            return self._extract_local(detection, raw, slug, ext)
        return self._extract_remote(detection, raw, slug, ext, context)

    # ── local ────────────────────────────────────────────────────────

    def _extract_local(
        self,
        detection: DetectionResult,
        path_str: str,
        slug: str,
        ext: str,
    ) -> ExtractionResult:
        path = Path(path_str)
        if not path.exists():
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"file not found: {path_str}",
            )
        return self._run_extractor(detection, str(path), slug, ext, source=path_str)

    # ── remote ───────────────────────────────────────────────────────

    def _extract_remote(
        self,
        detection: DetectionResult,
        url: str,
        slug: str,
        ext: str,
        context: ExtractionContext,
    ) -> ExtractionResult:
        tmp_path = context.work_dir / f"{slug}.{ext}"
        try:
            urllib.request.urlretrieve(url, str(tmp_path))
        except (OSError, urllib.error.URLError) as e:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"download failed: {e}",
            )
        return self._run_extractor(detection, str(tmp_path), slug, ext, source=url)

    # ── shared extractor ─────────────────────────────────────────────

    def _run_extractor(
        self,
        detection: DetectionResult,
        filepath: str,
        slug: str,
        ext: str,
        source: str,
    ) -> ExtractionResult:
        try:
            from arcus.provider_runtime.providers._shared import file_extract
        except ImportError as e:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"docs extractor unavailable (install [office] extra): {e}",
            )

        result = file_extract.extract_text(filepath, ext)
        text = (result or {}).get("text", "") or ""
        if not text.strip():
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["EXTRACTORS_EXHAUSTED"],
                error=f"{ext} extraction returned no text",
            )

        title = (result.get("title") or "").strip() or Path(filepath).stem
        authors = (result.get("authors") or "").strip() or None

        return ExtractionResult(
            status="success",
            kind="docs",
            extractor_detail={
                "extractor": _EXTRACTOR_NAME.get(ext, "unknown"),
                "ext": ext,
            },
            metadata=SourceMetadata(
                source=source,
                source_id=source,
                title=title,
                slug=slug,
                author=authors,
            ),
            text=text,
            segments=[],
            extracted_at=now_iso(),
        )

    def _failure(
        self,
        detection: DetectionResult,
        slug: str,
        *,
        exit_code: int,
        error: str,
    ) -> ExtractionResult:
        return ExtractionResult(
            status="failed",
            kind="docs",
            extractor_detail={},
            metadata=SourceMetadata(
                source=detection.raw,
                source_id=detection.source_id,
                title="",
                slug=slug,
            ),
            text="",
            segments=[],
            extracted_at=now_iso(),
            error=error,
            exit_code=exit_code,
        )
