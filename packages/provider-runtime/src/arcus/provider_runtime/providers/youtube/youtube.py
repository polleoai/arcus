"""YouTubeProvider: detects YouTube URLs, extracts transcripts via captions or NLM."""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path

from arcus.provider_runtime.log import now_iso
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.slug import disambiguate, make_slug
from arcus.provider_runtime.types import (
    EXIT_CODES,
    DetectionResult,
    ExtractionResult,
    SourceMetadata,
)

from .nlm_fallback import (
    NlmError,
    NlmNotAuthenticatedError,
    add_url_source,
    check_auth,
    create_notebook,
    delete_notebook,
    get_source_content,
    parse_transcript_to_segments,
)
from .nlm_limit import build_notebook_name, load_cached_limit
from .url import parse_youtube_url
from .vtt import build_paragraphs, parse_vtt
from .ytdlp_adapter import (
    RestrictedVideoError,
    YtDlpExtractionError,
    fetch_captions,
    fetch_metadata,
)


_NLM_LIMIT_FILE = Path.home() / ".config" / "arcus" / "limits.json"

# Module-level per-URL metadata cache so predict_slug + extract don't both
# pay the yt-dlp network round-trip. Cleared per-process; not persisted to disk.
_METADATA_CACHE: dict[str, "object"] = {}


def _cached_fetch_metadata(url: str):
    """Wraps ytdlp_adapter.fetch_metadata with a per-process URL cache."""
    cached = _METADATA_CACHE.get(url)
    if cached is not None:
        return cached
    meta = fetch_metadata(url)
    _METADATA_CACHE[url] = meta
    return meta


class YouTubeProvider:
    """Content provider for YouTube videos. Captions first, NLM fallback."""

    kind = "youtube"

    def matches(self, raw_input: str) -> DetectionResult | None:
        try:
            video_id = parse_youtube_url(raw_input)
        except ValueError:
            return None
        return DetectionResult(
            kind="youtube",
            source_id=video_id,
            raw=raw_input,
            metadata={"video_id": video_id},
        )

    def predict_slug(self, detection: DetectionResult) -> str:
        """Return the bare title-derived slug (no disambiguation suffix).

        Fetches metadata via the per-process cache so a subsequent
        `extract()` call doesn't re-pay the network round-trip.
        """
        meta = _cached_fetch_metadata(detection.raw)
        return make_slug(meta.title) or detection.source_id

    def extract(
        self,
        detection: DetectionResult,
        context: ExtractionContext,
    ) -> ExtractionResult:
        video_id = detection.metadata["video_id"]
        url = detection.raw

        try:
            context.emit_progress("fetching")
            meta = _cached_fetch_metadata(url)
        except RestrictedVideoError as e:
            return self._failure(
                detection,
                title=None,
                exit_code=EXIT_CODES["VIDEO_RESTRICTED"],
                error=f"video restricted: {e}",
            )
        except YtDlpExtractionError as e:
            return self._failure(
                detection,
                title=None,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"yt-dlp failed: {e}",
            )

        slug = disambiguate(make_slug(meta.title), video_id, context.out_dir)

        # Captions path
        if meta.subtitle_tracks:
            try:
                context.emit_progress("fetching")
                captions = fetch_captions(
                    url=url,
                    video_id=video_id,
                    preferred_lang=None,
                    available_tracks=meta.subtitle_tracks,
                    work_dir=context.work_dir,
                )
                context.emit_progress("extracting")
                segments = parse_vtt(captions.vtt_content)
                paragraphs = build_paragraphs(segments)
                body = "\n\n".join(paragraphs)

                return ExtractionResult(
                    status="success",
                    kind="youtube",
                    extractor_detail={
                        "caption_lang": captions.selected_track.lang,
                        "caption_source": captions.selected_track.source,
                    },
                    metadata=SourceMetadata(
                        source=url,
                        source_id=video_id,
                        title=meta.title,
                        slug=slug,
                        author=meta.channel,
                        duration_ms=meta.duration_ms,
                        posted=meta.posted,
                        language=captions.selected_track.lang,
                    ),
                    text=body,
                    segments=segments,
                    extracted_at=now_iso(),
                )
            except (YtDlpExtractionError, OSError):
                pass  # fall through to NLM

        # NLM fallback
        try:
            check_auth()
        except NlmNotAuthenticatedError as e:
            return self._failure(
                detection,
                title=meta.title,
                exit_code=EXIT_CODES["TOOL_NOT_AUTHENTICATED"],
                error=str(e),
            )

        notebook_id: str | None = None
        try:
            limit = load_cached_limit(_NLM_LIMIT_FILE)
            name = build_notebook_name(
                tag=context.notebook_tag,
                title=meta.title,
                video_id=video_id,
                date=_date.today().isoformat(),
                limit=limit,
            )
            notebook_id = create_notebook(name)
            context.emit_progress("fetching")
            # nlm source add --wait blocks until ingest completes — no separate poll.
            source_id = add_url_source(notebook_id, url)
            raw = get_source_content(source_id)
            context.emit_progress("extracting")
            segments = parse_transcript_to_segments(raw, meta.duration_ms)
            body = "\n\n".join(s.text for s in segments)

            # Success path: cleanup notebook
            try:
                delete_notebook(notebook_id)
            except NlmError:
                pass  # best-effort cleanup

            return ExtractionResult(
                status="success",
                kind="youtube",
                extractor_detail={},
                metadata=SourceMetadata(
                    source=url,
                    source_id=video_id,
                    title=meta.title,
                    slug=slug,
                    author=meta.channel,
                    duration_ms=meta.duration_ms,
                    posted=meta.posted,
                ),
                text=body,
                segments=segments,
                extracted_at=now_iso(),
            )
        except NlmError as e:
            # Keep notebook for forensics on failure (per spec).
            return self._failure(
                detection,
                title=meta.title,
                exit_code=EXIT_CODES["PROVIDER_FALLBACK_FAILED"],
                error=f"nlm: {e}",
            )

    def _failure(
        self,
        detection: DetectionResult,
        *,
        title: str | None,
        exit_code: int,
        error: str,
    ) -> ExtractionResult:
        slug = make_slug(title or "") or detection.source_id
        return ExtractionResult(
            status="failed",
            kind="youtube",
            extractor_detail={},
            metadata=SourceMetadata(
                source=detection.raw,
                source_id=detection.source_id,
                title=title or "",
                slug=slug,
            ),
            text="",
            segments=[],
            extracted_at=now_iso(),
            error=error,
            exit_code=exit_code,
        )
