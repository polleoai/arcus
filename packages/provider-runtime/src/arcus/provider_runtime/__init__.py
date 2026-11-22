"""arcus provider-runtime public API."""

from .factory import Factory, register_defaults
from .provider_interface import ExtractionContext, Provider
from .registry import ProviderRegistry
from .url_guard import UrlRefusedError, set_url_guard, using_url_guard
from .types import (
    EXIT_CODES,
    Asset,
    DetectionResult,
    ExtractionResult,
    FigureOptions,
    PdfLimits,
    Segment,
    SourceMetadata,
)
from .version import __version__

__all__ = [
    "EXIT_CODES",
    "__version__",
    "Asset",
    "DetectionResult",
    "ExtractionContext",
    "ExtractionResult",
    "Factory",
    "FigureOptions",
    "PdfLimits",
    "Provider",
    "ProviderRegistry",
    "Segment",
    "SourceMetadata",
    "UrlRefusedError",
    "register_defaults",
    "set_url_guard",
    "using_url_guard",
]
