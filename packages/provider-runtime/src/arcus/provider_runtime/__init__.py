"""arcus provider-runtime public API."""

from .factory import Factory, register_defaults
from .provider_interface import ExtractionContext, Provider
from .registry import ProviderRegistry
from .types import (
    EXIT_CODES,
    DetectionResult,
    ExtractionResult,
    Segment,
    SourceMetadata,
)

__all__ = [
    "EXIT_CODES",
    "DetectionResult",
    "ExtractionContext",
    "ExtractionResult",
    "Factory",
    "Provider",
    "ProviderRegistry",
    "Segment",
    "SourceMetadata",
    "register_defaults",
]
