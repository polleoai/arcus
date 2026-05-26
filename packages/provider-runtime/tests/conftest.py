"""Shared test fixtures.

The pdf/docs/image providers use Docling as their PRIMARY engine when the
`[docling]` extra is installed. Since CI / dev environments may or may not have
Docling installed, we default every test to the **lightweight fallback path**
(Docling OFF) so provider behavior is deterministic. Tests that specifically
exercise the Docling path opt in with `@pytest.mark.docling`.
"""

import pytest

from arcus.provider_runtime.providers._shared import docling_extract


@pytest.fixture(autouse=True)
def _docling_off(request, monkeypatch):
    if request.node.get_closest_marker("docling"):
        return  # docling-marked tests manage Docling themselves (mock or real)
    monkeypatch.setattr(docling_extract, "extract_markdown", lambda _fp: None)
