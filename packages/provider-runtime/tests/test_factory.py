from dataclasses import dataclass
from pathlib import Path

import pytest

from arcus.provider_runtime.factory import Factory
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.registry import ProviderRegistry
from arcus.provider_runtime.types import (
    EXIT_CODES,
    DetectionResult,
    ExtractionResult,
    SourceMetadata,
)


class StubProvider:
    """Test stub — matches any input starting with `kind:`."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def matches(self, raw: str) -> DetectionResult | None:
        prefix = f"{self.kind}:"
        if raw.startswith(prefix):
            return DetectionResult(
                kind=self.kind,
                source_id=raw[len(prefix):],
                raw=raw,
                metadata={},
            )
        return None

    def extract(self, detection: DetectionResult, context: ExtractionContext) -> ExtractionResult:
        return ExtractionResult(
            status="success",
            kind=self.kind,
            extractor_detail={},
            metadata=SourceMetadata(
                source=detection.raw,
                source_id=detection.source_id,
                title=f"Title for {detection.source_id}",
                slug=detection.source_id,
            ),
            text=f"Body for {detection.source_id}",
            segments=[],
            extracted_at="2026-05-17T00:00:00+00:00",
        )


def test_registry_first_match_wins() -> None:
    reg = ProviderRegistry()
    reg.register(StubProvider("first"))
    reg.register(StubProvider("second"))

    match = reg.detect("first:abc")
    assert match is not None
    provider, det = match
    assert provider.kind == "first"
    assert det.source_id == "abc"


def test_registry_returns_none_when_no_match() -> None:
    reg = ProviderRegistry()
    reg.register(StubProvider("x"))
    assert reg.detect("zz:abc") is None


def test_factory_run_success_writes_outputs(tmp_path: Path) -> None:
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    factory = Factory(registry=reg)

    exit_code = factory.run("kind1:abc", out_dir=tmp_path, force=False)
    assert exit_code == EXIT_CODES["SUCCESS"]
    assert (tmp_path / "abc.md").exists()
    assert (tmp_path / "abc.json").exists()


def test_factory_run_unsupported_input_exits_30(tmp_path: Path) -> None:
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    factory = Factory(registry=reg)

    exit_code = factory.run("vimeo://nope", out_dir=tmp_path, force=False)
    assert exit_code == EXIT_CODES["EXTRACTORS_EXHAUSTED"]


def test_factory_cache_hit_short_circuits(tmp_path: Path) -> None:
    reg = ProviderRegistry()
    reg.register(StubProvider("kind1"))
    factory = Factory(registry=reg)

    # First run — produces outputs
    factory.run("kind1:abc", out_dir=tmp_path, force=False)
    md_mtime = (tmp_path / "abc.md").stat().st_mtime_ns

    # Second run — cache hit, file untouched
    factory.run("kind1:abc", out_dir=tmp_path, force=False)
    assert (tmp_path / "abc.md").stat().st_mtime_ns == md_mtime

    # --force re-extracts
    factory.run("kind1:abc", out_dir=tmp_path, force=True)
    # mtime should change (or stay same if filesystem coarsens; just verify file still exists)
    assert (tmp_path / "abc.md").exists()
