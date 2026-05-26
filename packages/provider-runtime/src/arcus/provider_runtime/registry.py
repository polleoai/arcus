"""ProviderRegistry: ordered list of providers; first match wins."""

from __future__ import annotations

from .provider_interface import Provider
from .types import DetectionResult


class ProviderRegistry:
    """Ordered registry of content providers."""

    def __init__(self) -> None:
        self._providers: list[Provider] = []

    def register(self, provider: Provider) -> None:
        """Append a provider. Order matters: earlier providers match first."""
        self._providers.append(provider)

    def all(self) -> list[Provider]:
        return list(self._providers)

    def get(self, kind: str) -> Provider | None:
        """Return the registered provider with this kind, or None."""
        for p in self._providers:
            if p.kind == kind:
                return p
        return None

    def detect(self, raw_input: str) -> tuple[Provider, DetectionResult] | None:
        """Return (provider, detection) for the first provider whose .matches() succeeds."""
        for p in self._providers:
            d = p.matches(raw_input)
            if d is not None:
                return p, d
        return None
