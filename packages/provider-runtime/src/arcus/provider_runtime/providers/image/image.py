"""ImageProvider: OCR an image into normalized text via Tesseract.

Local images are read directly; remote image URLs are downloaded into the
work_dir then OCR'd (same local-or-remote shape as the PDF/Docs providers).

OCR backend: RapidOCR (ONNX Runtime) via the `rapidocr-onnxruntime` package in
the optional `[image]` extra. It is **pure-pip** — it bundles its own models and
runtime, so there is no system binary to install — and runs fully locally (zero
network egress), so this provider is sandbox-friendly. The OCR call is isolated
in `_ocr()` so the backend can be swapped (e.g. Tesseract) without touching the
provider contract. v1 emits plain text only: `structured` is False and `locators`
is empty (bounding-box locators are a future enhancement — RapidOCR already
returns per-line boxes, so they can be surfaced later).
"""

from __future__ import annotations

import re
import urllib.error
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
_SUPPORTED_EXTS = ("png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif")
_EXT_PATTERN = re.compile(rf"\.({'|'.join(_SUPPORTED_EXTS)})(\?|$)", re.IGNORECASE)
_HEADING = re.compile(r"^#+\s*")


class OcrUnavailableError(RuntimeError):
    """Raised when the OCR backend (the `[image]` extra) is not installed."""


# RapidOCR loads ONNX models on construction (~seconds), so reuse one engine
# across calls. Module-level + lazy so import is cheap and the cost is paid once.
_engine = None


def _ocr(filepath: str) -> str:
    """Run RapidOCR on an image file and return the recognized text.

    Raises OcrUnavailableError when the `[image]` extra (rapidocr-onnxruntime) is
    not installed, so the provider can surface an actionable message instead of a
    raw ImportError. Pure-pip backend — no system binary required.
    """
    global _engine
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        raise OcrUnavailableError(
            "image OCR needs the [image] extra: "
            "pip install 'arcus-provider-runtime[image]'"
        ) from e
    if _engine is None:
        _engine = RapidOCR()
    result, _elapse = _engine(filepath)
    # result is a list of [box, text, score] in reading order, or None/empty.
    if not result:
        return ""
    return "\n".join(line[1] for line in result)


def _is_http(s: str) -> bool:
    return bool(_HTTP_SCHEME.match(s))


def _detect_ext(path_or_url: str) -> str | None:
    path = urlparse(path_or_url).path if _is_http(path_or_url) else path_or_url
    m = _EXT_PATTERN.search(path)
    return m.group(1).lower() if m else None


def _input_to_slug(raw_input: str) -> str:
    if _is_http(raw_input):
        parsed = urlparse(raw_input)
        last = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        stem = Path(last).stem
        return make_slug(stem) or make_slug(parsed.netloc) or "image"
    return make_slug(Path(raw_input).stem) or "image"


def _title_from(text: str, fallback: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        return _HEADING.sub("", line).strip()[:80] or fallback
    return fallback


class ImageProvider:
    """OCR-based extraction for local + remote image files."""

    kind = "image"

    def matches(self, raw_input: str) -> DetectionResult | None:
        if not isinstance(raw_input, str) or not raw_input:
            return None
        ext = _detect_ext(raw_input)
        if ext is None:
            return None
        return DetectionResult(
            kind="image",
            source_id=raw_input,
            raw=raw_input,
            metadata={"is_local": not _is_http(raw_input), "ext": ext},
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
        if detection.metadata.get("is_local", True):
            path = Path(raw)
            if not path.exists():
                return self._failure(
                    detection, slug, EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                    f"file not found: {raw}",
                )
            return self._run_ocr(detection, str(path), slug, source=raw, context=context)

        ext = detection.metadata.get("ext", "png")
        tmp_path = context.work_dir / f"{slug}.{ext}"
        context.emit_progress("fetching")
        try:
            urllib.request.urlretrieve(raw, str(tmp_path))
        except (OSError, urllib.error.URLError) as e:
            return self._failure(
                detection, slug, EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                f"download failed: {e}",
            )
        return self._run_ocr(detection, str(tmp_path), slug, source=raw, context=context)

    def _run_ocr(
        self,
        detection: DetectionResult,
        filepath: str,
        slug: str,
        *,
        source: str,
        context: ExtractionContext,
    ) -> ExtractionResult:
        context.emit_progress("extracting")
        try:
            text = _ocr(filepath)
        except OcrUnavailableError as e:
            return self._failure(
                detection, slug, EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                f"image OCR unavailable: {e}",
            )
        except Exception as e:  # malformed image, decode error, etc.
            return self._failure(
                detection, slug, EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                f"OCR failed: {e}",
            )

        text = (text or "").strip()
        if not text:
            return self._failure(
                detection, slug, EXIT_CODES["EXTRACTORS_EXHAUSTED"],
                "OCR produced no text (image may have no legible text)",
            )

        return ExtractionResult(
            status="success",
            kind="image",
            extractor_detail={"extractor": "rapidocr", "structured": False, "locators": []},
            metadata=SourceMetadata(
                source=source,
                source_id=source,
                title=_title_from(text, Path(filepath).stem),
                slug=slug,
            ),
            text=text,
            segments=[],
            extracted_at=now_iso(),
        )

    def _failure(self, detection, slug, exit_code, error) -> ExtractionResult:
        return ExtractionResult(
            status="failed",
            kind="image",
            extractor_detail={},
            metadata=SourceMetadata(
                source=detection.raw, source_id=detection.source_id, title="", slug=slug,
            ),
            text="",
            segments=[],
            extracted_at=now_iso(),
            error=error,
            exit_code=exit_code,
        )
