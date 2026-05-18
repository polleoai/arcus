"""Provider Protocol — every content-extraction provider implements this shape.

This file is documentation by code. Concrete providers live under
`providers/<kind>/`. The Protocol is `@runtime_checkable` so the factory
can `isinstance(p, Provider)` for sanity in tests, though duck-typing is
the contract; the Protocol is not enforced beyond static type checking.

Lifecycle:
  1. Factory.detect(input) walks registered providers calling .matches(input).
     First non-None DetectionResult wins.
  2. Caller passes detection to provider.extract(detection, context).
  3. Provider returns ExtractionResult; caller writes via shared writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .types import DetectionResult, ExtractionResult


@dataclass
class ExtractionContext:
    """Context passed to provider.extract().

    Carries shared state (output dir, work dir for intermediates) plus
    references that composite providers (athena_topic) need to recurse —
    namely the factory itself, so a composite can dispatch its children
    through the same registry.
    """

    out_dir: Path
    work_dir: Path
    notebook_tag: str | None = None
    keep_intermediates: bool = False
    # Set by the factory before passing into composite providers. None for
    # leaf providers that don't recurse.
    factory: "Factory | None" = None  # forward-ref to break import cycle


@runtime_checkable
class Provider(Protocol):
    """Single content-extraction provider."""

    kind: str
    """Stable identifier — e.g. 'youtube', 'html', 'pdf', 'athena_topic'."""

    def matches(self, raw_input: str) -> DetectionResult | None:
        """Pure: return parsed detection if this provider handles the input.

        No network, no file IO. Detection uses string shape only.
        """
        ...

    def extract(
        self,
        detection: DetectionResult,
        context: ExtractionContext,
    ) -> ExtractionResult:
        """Fetch + transform the content. Network IO + filesystem allowed.

        Returns ExtractionResult with status='success' or 'failed'.
        Composite providers may populate `children` for nested results.
        """
        ...


# `ExtractionContext.factory` is typed as the string `"Factory | None"`.
# We deliberately do NOT import Factory at module load time — `from __future__
# import annotations` keeps every annotation as a string, and dataclasses
# never resolves it. Composite providers that need the live class can
# `from .factory import Factory` lazily inside a method body without
# creating an import cycle.
