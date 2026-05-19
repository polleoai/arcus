"""HtmlProvider: generic web pages + X.com tweets, routed through the same provider.

Dispatch:
  - matches() — accepts http(s) URLs that aren't YouTube and don't end in .pdf.
    Stamps `metadata.is_xcom` based on the URL host so extract() can route.
  - predict_slug() — URL-derived (no network). Filename stays stable across runs
    so cache_hit_exists() works without a metadata round-trip. The page's actual
    <title> goes in frontmatter, not in the slug.
  - extract() — dispatches by `is_xcom`. Generic path wraps fetch_page (returns
    str). XCom path wraps fetch_x_tweet (returns {text, images}); the images
    list rides as extractor_detail since arcus has no inline-image storage.
"""

from __future__ import annotations

import re
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
_YOUTUBE_HOST = re.compile(r"^https?://(www\.)?(youtube\.com|youtu\.be)/", re.IGNORECASE)
_PDF_SUFFIX = re.compile(r"\.pdf(\?|$)", re.IGNORECASE)
_XCOM_HOST = re.compile(r"^https?://(www\.)?(x\.com|twitter\.com)/", re.IGNORECASE)


def _url_to_slug(url: str) -> str:
    """Deterministic URL → slug. No network. Drops query + fragment."""
    parsed = urlparse(url)
    parts = [parsed.netloc, parsed.path]
    text = " ".join(p for p in parts if p)
    slug = make_slug(text)
    return slug or "page"


def _looks_like_login_wall(text: str) -> bool:
    """Conservative login-wall heuristic. False negatives over false positives.

    Trips when:
      - "Join LinkedIn" appears (LinkedIn's strongest single-string modal marker), or
      - text is short (<500 chars) AND contains both "Sign in" and "Sign up"
        (X.com auth wall pattern). Real articles are long enough not to trip.
    """
    if "Join LinkedIn" in text:
        return True
    if len(text) < 500:
        has_in = "Sign in" in text or "Log in" in text
        has_up = "Sign up" in text
        if has_in and has_up:
            return True
    return False


_HEADING_PREFIX = re.compile(r"^#+\s*")


def _derive_title(body: str, fallback_slug: str) -> str:
    """Pick a title from body: first non-empty line, stripping any markdown
    `#`-heading markers. Empty headings (just hash chars) are skipped. Falls
    back to the URL-derived slug when nothing usable is found. Cap 80 chars."""
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        cleaned = _HEADING_PREFIX.sub("", stripped).strip()
        if cleaned:
            return cleaned[:80]
    return fallback_slug


class HtmlProvider:
    """Generic HTML + X.com extraction. Single provider, dispatch-by-metadata."""

    kind = "html"

    def matches(self, raw_input: str) -> DetectionResult | None:
        if not isinstance(raw_input, str) or not _HTTP_SCHEME.match(raw_input):
            return None
        if _YOUTUBE_HOST.match(raw_input):
            return None
        if _PDF_SUFFIX.search(urlparse(raw_input).path):
            return None
        is_xcom = bool(_XCOM_HOST.match(raw_input))
        return DetectionResult(
            kind="html",
            source_id=raw_input,
            raw=raw_input,
            metadata={"is_xcom": is_xcom},
        )

    def predict_slug(self, detection: DetectionResult) -> str:
        return _url_to_slug(detection.raw)

    def extract(
        self,
        detection: DetectionResult,
        context: ExtractionContext,
    ) -> ExtractionResult:
        # Lazy import — keeps module-load light when neither Playwright nor
        # node are installed.
        from arcus.provider_runtime.providers.html import _athena_fetch_page

        url = detection.raw
        slug = _url_to_slug(url)
        is_xcom = detection.metadata.get("is_xcom", False)

        if is_xcom:
            return self._extract_xcom(detection, url, slug, _athena_fetch_page)
        return self._extract_generic(detection, url, slug, _athena_fetch_page)

    def _extract_generic(
        self,
        detection: DetectionResult,
        url: str,
        slug: str,
        fetch_module,
    ) -> ExtractionResult:
        try:
            body = fetch_module.fetch_page(url)
        except Exception as e:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"fetch_page raised: {e}",
            )

        if not body or not body.strip():
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error="fetch_page returned no content",
            )

        if _looks_like_login_wall(body):
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["TOOL_NOT_AUTHENTICATED"],
                error="login wall detected — consumer should fall back to authenticated capture",
            )

        title = _derive_title(body, slug)
        return ExtractionResult(
            status="success",
            kind="html",
            extractor_detail={"extractor": "fetch_page"},
            metadata=SourceMetadata(
                source=url,
                source_id=url,
                title=title,
                slug=slug,
            ),
            text=body.strip(),
            segments=[],
            extracted_at=now_iso(),
        )

    def _extract_xcom(
        self,
        detection: DetectionResult,
        url: str,
        slug: str,
        fetch_module,
    ) -> ExtractionResult:
        try:
            result = fetch_module.fetch_x_tweet(url)
        except Exception as e:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"fetch_x_tweet raised: {e}",
            )

        text = (result or {}).get("text", "")
        images = (result or {}).get("images", [])

        if not text or not text.strip():
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error="fetch_x_tweet returned no text",
            )

        title = _derive_title(text, slug)
        return ExtractionResult(
            status="success",
            kind="html",
            extractor_detail={"extractor": "fetch_x_tweet", "images": images},
            metadata=SourceMetadata(
                source=url,
                source_id=url,
                title=title,
                slug=slug,
            ),
            text=text.strip(),
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
            kind="html",
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
