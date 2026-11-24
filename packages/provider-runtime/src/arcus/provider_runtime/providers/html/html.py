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
from arcus.provider_runtime.url_guard import UrlRefusedError
from arcus.provider_runtime.types import (
    EXIT_CODES,
    DetectionResult,
    ExtractionResult,
    Segment,
    SourceMetadata,
)


_HTTP_SCHEME = re.compile(r"^https?://", re.IGNORECASE)
_YOUTUBE_HOST = re.compile(r"^https?://(www\.)?(youtube\.com|youtu\.be)/", re.IGNORECASE)
_PDF_SUFFIX = re.compile(r"\.pdf(\?|$)", re.IGNORECASE)
_XCOM_HOST = re.compile(r"^https?://(www\.)?(x\.com|twitter\.com)/", re.IGNORECASE)
# reveal.js mis-extraction fingerprint: the generic html2md path renders a deck
# either empty or with raw HTML entities left in the body. Either signal gates a
# cheap static-HTML re-fetch + section-walk (see _extract_generic). Clean article
# captures never carry these entities, so they pay nothing.
_REVEAL_SMELL_RE = re.compile(r"&(?:middot|amp|lt|gt|nbsp|#\d+);", re.IGNORECASE)


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
        slug = context.slug or _url_to_slug(url)
        is_xcom = detection.metadata.get("is_xcom", False)

        if is_xcom:
            return self._extract_xcom(detection, url, slug, _athena_fetch_page, context)
        return self._extract_generic(detection, url, slug, _athena_fetch_page, context)

    def _extract_generic(
        self,
        detection: DetectionResult,
        url: str,
        slug: str,
        fetch_module,
        context: ExtractionContext,
    ) -> ExtractionResult:
        try:
            context.emit_progress("fetching")
            body = fetch_module.fetch_page(url)
        except UrlRefusedError:
            raise
        except Exception as e:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"fetch_page raised: {e}",
            )

        body = body or ""

        # reveal.js slide-deck recovery. The generic path mis-extracts decks
        # (empty body, or markdown still carrying raw HTML entities). Gate the
        # extra static-HTML fetch on that cheap signal so clean captures pay
        # nothing, then section-walk the deck into discrete per-slide segments.
        if not body.strip() or _REVEAL_SMELL_RE.search(body):
            deck_result = self._extract_slide_deck(detection, url, slug, context)
            if deck_result is not None:
                return deck_result

        if not body.strip():
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

        context.emit_progress("extracting")
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

    def _extract_slide_deck(
        self,
        detection: DetectionResult,
        url: str,
        slug: str,
        context: ExtractionContext,
    ) -> ExtractionResult | None:
        """Recover a reveal.js deck as clean per-slide text.

        Re-fetches the static server HTML (no browser — reveal slide text lives
        in the served <section>s) and runs the depth-aware leaf-section walk.
        Returns a success ExtractionResult with one Segment per slide (mirroring
        the docs provider's per-sheet/per-slide segments — the slide number
        rides in extractor_detail["locators"], not in the timing fields), or
        None to fall through to the normal generic path when the page is not a
        reveal.js deck.
        """
        from arcus.provider_runtime.providers.html import slide_deck

        raw_html = slide_deck.fetch_html(url)
        if not raw_html:
            return None
        deck = slide_deck.parse_deck(raw_html, url=url)
        if deck is None:
            return None

        context.emit_progress("extracting")
        segments = [Segment(start_ms=0, end_ms=0, text=s) for s in deck.slides]
        locators = [{"segment": i, "slide": i + 1} for i in range(len(deck.slides))]
        title = deck.title or _derive_title(deck.markdown, slug)
        return ExtractionResult(
            status="success",
            kind="html",
            extractor_detail={
                "extractor": "reveal_slide_deck",
                "slides": len(deck.slides),
                "locators": locators,
            },
            metadata=SourceMetadata(
                source=url,
                source_id=url,
                title=title,
                slug=slug,
            ),
            text=deck.markdown,
            segments=segments,
            extracted_at=now_iso(),
        )

    def _extract_xcom(
        self,
        detection: DetectionResult,
        url: str,
        slug: str,
        fetch_module,
        context: ExtractionContext,
    ) -> ExtractionResult:
        """X posts. Without a logged-in session, X refuses headless browsers,
        so the post comes from X's public syndication endpoint (complete for
        short posts; long posts are flagged truncated, reason "no_session")."""
        from arcus.provider_runtime.providers.html import x_syndication

        context.emit_progress("fetching")
        tid = x_syndication.tweet_id(url)
        if tid is None:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error="x post unavailable: no status id in the URL",
            )
        try:
            post = x_syndication.parse(x_syndication.fetch_json(tid), tid)
        except UrlRefusedError:
            raise
        except x_syndication.XPostUnavailable as e:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"x post unavailable: {e}",
            )
        except Exception as e:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"x syndication failed: {type(e).__name__}: {e}",
            )

        text = post["text"]
        if not text.strip():
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error="x syndication returned no text",
            )

        context.emit_progress("extracting")
        detail = {
            "extractor": "x_syndication",
            "images": post["images"],
            "truncated": post["truncated"],
        }
        if post["truncated"]:
            detail["truncated_reason"] = "no_session"
            detail["truncation_signal"] = post["signal"]
        return ExtractionResult(
            status="success",
            kind="html",
            extractor_detail=detail,
            metadata=SourceMetadata(
                source=url,
                source_id=url,
                title=_derive_title(text, slug),
                slug=slug,
                author=post["author"],
                posted=post["posted"],
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
