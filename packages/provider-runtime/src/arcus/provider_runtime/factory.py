"""Factory: end-to-end orchestration of detection → cache check → extract → write."""

from __future__ import annotations

import re
import tempfile
import traceback
from pathlib import Path

from .log import EventLogger, now_iso
from .provider_interface import ExtractionContext
from .registry import ProviderRegistry
from .slug import make_slug
from .types import EXIT_CODES, ExtractionLimits, FigureOptions, PdfLimits
from .url_guard import UrlRefusedError
from .writer import SlugTakenError, cache_hit_exists, claim_slug, write_failure_stub, write_success


class Factory:
    """Owns a ProviderRegistry; provides `.run(input, out_dir)` for one-shot extraction."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    def detect(self, raw_input: str):
        return self.registry.detect(raw_input)

    def run(
        self,
        raw_input: str,
        *,
        out_dir: Path,
        force: bool = False,
        json_log: bool = False,
        keep_intermediates: bool = False,
        notebook_tag: str | None = None,
        provider: str | None = None,
        figures: FigureOptions | None = None,
        pdf_limits: PdfLimits | None = None,
        extraction_limits: ExtractionLimits | None = None,
    ) -> int:
        """Detect → cache check → extract → write outputs. Returns exit code.

        When `provider` is given, auto-detection is skipped and that provider
        kind is forced (exit 11 if it doesn't match, exit 2 if it's unknown).
        """
        logger = EventLogger(out_dir, json_log_stderr=json_log)
        logger.stage("started", raw=raw_input)

        if provider is not None:
            forced = self.registry.get(provider)
            if forced is None:
                valid = ", ".join(p.kind for p in self.registry.all())
                logger.stage("failed", raw=raw_input,
                             error=f"unknown provider kind {provider!r}; valid kinds: {valid}")
                return EXIT_CODES["INVALID_ARGS"]
            detection = forced.matches(raw_input)
            if detection is None:
                logger.stage("failed", kind=forced.kind, raw=raw_input,
                             error=f"forced provider {provider!r} does not match input")
                return EXIT_CODES["PROVIDER_FORCED_NO_MATCH"]
            match = (forced, detection)
        else:
            match = self.registry.detect(raw_input)
            if match is None:
                logger.stage("failed", raw=raw_input, error="no provider matched")
                return EXIT_CODES["EXTRACTORS_EXHAUSTED"]

        provider_obj, detection = match
        logger.stage("detected", kind=provider_obj.kind, source_id=detection.source_id)

        # Cache check uses the provider's predicted slug. If the on-disk
        # file's frontmatter `source_id` matches this detection's source_id,
        # we trust the file and short-circuit. Disambiguated forms
        # (`<slug>--<8char>.md`) are checked too — see cache_hit_exists.
        try:
            predicted_slug = provider_obj.predict_slug(detection)
        except Exception as e:
            # predict_slug failed (e.g., metadata fetch hit network error).
            # Fall through to extraction — the real extract() call will
            # surface the same error in a structured way.
            logger.emit({
                "ts": now_iso(),
                "event": "detected",
                "kind": provider_obj.kind,
                "source_id": detection.source_id,
                "warning": "predict_slug_failed",
                "error": str(e),
            })
            predicted_slug = None

        hit_md = (
            cache_hit_exists(out_dir, predicted_slug, detection.source_id)
            if (not force and predicted_slug is not None)
            else None
        )
        if hit_md is not None:
            # `hit_md` is the ACTUAL matched file (already resolved), which may
            # be a disambiguated form `<slug>--<hash>.md` — not the bare slug.
            # Derive the .json sibling from it so the emitted paths point to
            # files that genuinely exist on disk (R7).
            hit_json = hit_md.with_suffix(".json")
            logger.stage(
                "cache_hit",
                kind=provider_obj.kind,
                source_id=detection.source_id,
                slug=hit_md.stem,
                md_path=str(hit_md),
                json_path=str(hit_json.resolve()),
            )
            return EXIT_CODES["SUCCESS"]

        # Sanitize source_id for use as a tempdir prefix — URLs and local
        # paths both contain '/' which mkdtemp interprets as a path separator.
        safe_prefix = re.sub(r"[^A-Za-z0-9._-]", "_", detection.source_id)[:40]
        with tempfile.TemporaryDirectory(prefix=f"arcus-{safe_prefix}-") as tmp:
            def _emit_progress(stage: str) -> None:
                logger.stage(stage, kind=provider_obj.kind, source_id=detection.source_id)

            # Claim the output name before extracting: two sources whose
            # slugs collide (two `paper.pdf`) must never share md/json/figures.
            try:
                claimed = (claim_slug(out_dir, predicted_slug, detection.source_id)
                           if predicted_slug else None)
            except SlugTakenError as e:
                return self._refused_write(logger, provider_obj, detection, e)
            context = ExtractionContext(
                out_dir=out_dir,
                slug=claimed,
                work_dir=Path(tmp),
                notebook_tag=notebook_tag,
                keep_intermediates=keep_intermediates,
                factory=self,
                emit_progress=_emit_progress,
                figures=figures if figures is not None else FigureOptions(),
                pdf_limits=pdf_limits if pdf_limits is not None else PdfLimits(),
                limits=extraction_limits if extraction_limits is not None else ExtractionLimits(),
            )
            try:
                result = provider_obj.extract(detection, context)
            except Exception as e:  # provider-level uncaught — never crash the CLI
                # A caller URL-guard refusal is an expected, reportable outcome
                # (its own exit code, `refused_url`), not an "unhandled" crash.
                refused = isinstance(e, UrlRefusedError)
                exit_code = EXIT_CODES["URL_REFUSED" if refused else "PROVIDER_PRIMARY_FAILED"]
                extra = {"refused_url": e.url} if refused else {"traceback": traceback.format_exc()}
                logger.stage(
                    "failed",
                    kind=provider_obj.kind,
                    source_id=detection.source_id,
                    error=str(e) if refused else f"unhandled: {e}",
                    **extra,
                )
                # `detection.source_id` may be a full URL (remote providers),
                # which contains '/' and would make write_failure_stub's
                # `<slug>.md` write blow up with FileNotFoundError — escaping
                # the never-crash guarantee. Sanitize to a filesystem-safe
                # slug, and wrap the stub write so a stub-write failure can
                # NEVER re-raise out of this path (the `failed` event has
                # already been emitted; we must still return the exit code).
                try:
                    safe_slug = claim_slug(
                        out_dir, make_slug(detection.source_id) or "extraction-failed",
                        detection.source_id)
                    write_failure_stub(
                        out_dir,
                        slug=safe_slug,
                        source=detection.raw,
                        source_id=detection.source_id,
                        kind=provider_obj.kind,
                        title=None,
                        exit_code=exit_code,
                        extractor_attempted=[provider_obj.kind],
                        error=str(e),
                    )
                except Exception:
                    pass
                return exit_code

        if result.status == "success":
            try:
                md_path, json_path = write_success(out_dir, result.metadata.slug, result)
            except SlugTakenError as e:
                return self._refused_write(logger, provider_obj, detection, e)
            logger.stage(
                "success",
                kind=provider_obj.kind,
                source_id=detection.source_id,
                slug=result.metadata.slug,
                md_path=str(md_path),
                json_path=str(json_path),
            )
            return EXIT_CODES["SUCCESS"]

        # status == "failed". The stub goes under this source's claimed name
        # (where the cache check looks), never over another source's output.
        try:
            stub_slug = claimed or claim_slug(out_dir, result.metadata.slug, detection.source_id)
            write_failure_stub(
                out_dir,
                slug=stub_slug,
                source=detection.raw,
                source_id=detection.source_id,
                kind=provider_obj.kind,
                title=result.metadata.title or None,
                exit_code=result.exit_code or EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                extractor_attempted=[provider_obj.kind],
                error=result.error or "unknown failure",
            )
        except SlugTakenError as e:
            return self._refused_write(logger, provider_obj, detection, e)
        logger.stage(
            "failed",
            kind=provider_obj.kind,
            source_id=detection.source_id,
            slug=stub_slug,
            error=result.error,
        )
        return result.exit_code or EXIT_CODES["PROVIDER_PRIMARY_FAILED"]

    @staticmethod
    def _refused_write(logger, provider_obj, detection, err: SlugTakenError) -> int:
        """The provider named an output another source holds: report it, write
        nothing (the other source's files stay as they are)."""
        logger.stage(
            "failed",
            kind=provider_obj.kind,
            source_id=detection.source_id,
            slug=err.slug,
            error=str(err),
        )
        return EXIT_CODES["PROVIDER_PRIMARY_FAILED"]


def register_defaults(registry: ProviderRegistry) -> None:
    """Register the v1 providers.

    Dispatch order matters — first-match-wins:
      1. YouTube (specific host pattern)
      2. PDF (specific file suffix; must run before HTML so .pdf URLs
         don't get caught by HTML's broad scheme match)
      3. Docs (specific file suffixes — docx/xlsx/pptx/epub; same reason
         as PDF — register before HTML)
      4. Text (local .md/.txt/.markdown/.text — never matches http; safe
         to register before the HTML catch-all)
      5. Image (specific image suffixes — png/jpg/…; register before HTML so
         image URLs don't get caught by HTML's broad scheme match)
      6. HTML (catch-all for any other http(s) URL)
    """
    from .providers.docs.docs import DocsProvider
    from .providers.html.html import HtmlProvider
    from .providers.image.image import ImageProvider
    from .providers.pdf.pdf import PdfProvider
    from .providers.text.text import TextProvider
    from .providers.youtube.youtube import YouTubeProvider
    registry.register(YouTubeProvider())
    registry.register(PdfProvider())
    registry.register(DocsProvider())
    registry.register(TextProvider())   # local .md/.txt only — never matches http
    registry.register(ImageProvider())  # png/jpg/… (local + remote) — before HTML
    registry.register(HtmlProvider())
