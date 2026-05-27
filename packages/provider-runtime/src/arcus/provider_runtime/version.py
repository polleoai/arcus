"""Single source of the runtime version, read from package metadata."""

from __future__ import annotations

import importlib.metadata

try:
    __version__ = importlib.metadata.version("arcus-provider-runtime")
except importlib.metadata.PackageNotFoundError:  # uninstalled checkout
    __version__ = "0.6.0"
