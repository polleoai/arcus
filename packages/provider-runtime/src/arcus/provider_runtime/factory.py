"""Factory: end-to-end orchestration of detection → cache check → extract → write."""

from __future__ import annotations

import tempfile
import traceback
from pathlib import Path

from .log import EventLogger, now_iso
from .provider_interface import ExtractionContext, Provider
from .registry import ProviderRegistry
from .types import EXIT_CODES
from .writer import cache_hit_exists, write_failure_stub, write_success


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
    ) -> int:
        """Detect → cache check → extract → write outputs. Returns exit code."""
        logger = EventLogger(out_dir, json_log_stderr=json_log)
        logger.emit({"ts": now_iso(), "raw": raw_input, "status": "started"})

        match = self.registry.detect(raw_input)
        if match is None:
            logger.emit({"ts": now_iso(), "raw": raw_input, "status": "failed",
                         "error": "no provider matched"})
            return EXIT_CODES["EXTRACTORS_EXHAUSTED"]

        provider, detection = match
        logger.emit({
            "ts": now_iso(),
            "kind": provider.kind,
            "source_id": detection.source_id,
            "event": "detected",
        })

        # Cache check uses the bare source_id as a "best guess" slug; the real
        # slug is determined by the provider during extraction. If a file
        # already exists with the *source_id-based* slug, we trust it.
        if not force and cache_hit_exists(out_dir, detection.source_id):
            logger.emit({
                "ts": now_iso(),
                "kind": provider.kind,
                "source_id": detection.source_id,
                "status": "cache_hit",
            })
            return EXIT_CODES["SUCCESS"]

        with tempfile.TemporaryDirectory(prefix=f"arcus-{detection.source_id}-") as tmp:
            context = ExtractionContext(
                out_dir=out_dir,
                work_dir=Path(tmp),
                notebook_tag=notebook_tag,
                keep_intermediates=keep_intermediates,
                factory=self,
            )
            try:
                result = provider.extract(detection, context)
            except Exception as e:  # provider-level uncaught — never crash the CLI
                tb = traceback.format_exc()
                logger.emit({
                    "ts": now_iso(),
                    "kind": provider.kind,
                    "source_id": detection.source_id,
                    "status": "failed",
                    "error": f"unhandled: {e}",
                    "traceback": tb,
                })
                write_failure_stub(
                    out_dir,
                    slug=detection.source_id,
                    source=detection.raw,
                    source_id=detection.source_id,
                    kind=provider.kind,
                    title=None,
                    exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                    extractor_attempted=[provider.kind],
                    error=str(e),
                )
                return EXIT_CODES["PROVIDER_PRIMARY_FAILED"]

        if result.status == "success":
            write_success(out_dir, result.metadata.slug, result)
            logger.emit({
                "ts": now_iso(),
                "kind": provider.kind,
                "source_id": detection.source_id,
                "status": "success",
                "slug": result.metadata.slug,
            })
            return EXIT_CODES["SUCCESS"]

        # status == "failed"
        write_failure_stub(
            out_dir,
            slug=result.metadata.slug,
            source=detection.raw,
            source_id=detection.source_id,
            kind=provider.kind,
            title=result.metadata.title or None,
            exit_code=result.exit_code or EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
            extractor_attempted=[provider.kind],
            error=result.error or "unknown failure",
        )
        logger.emit({
            "ts": now_iso(),
            "kind": provider.kind,
            "source_id": detection.source_id,
            "status": "failed",
            "error": result.error,
        })
        return result.exit_code or EXIT_CODES["PROVIDER_PRIMARY_FAILED"]


def register_defaults(registry: ProviderRegistry) -> None:
    """Register the providers shipped in Plan A.0 (just YouTube for now)."""
    from .providers.youtube.youtube import YouTubeProvider
    registry.register(YouTubeProvider())
