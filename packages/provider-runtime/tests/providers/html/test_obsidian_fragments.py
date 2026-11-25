"""#23 (security review): a fence can't be assembled from separately escaped pieces.

A tilde run split across text fragments (an empty <code> between them, a tag
that splits a parser data chunk, escaped pieces joined later) must not come
together as a "~~~dataviewjs" line: every "~" in page text is escaped.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from arcus.provider_runtime.providers._shared.markdown_text import escape_md_text
from arcus.provider_runtime.providers.html import _athena_fetch_page as fp
from arcus.provider_runtime.providers.html import slide_deck

HTML2MD = Path(fp.__file__).with_name("html2md.mjs")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="html2md needs node")
FENCE_LINE = re.compile(r"^(?:[ \t>]|[-*+][ \t]|\d{1,9}[.)][ \t])*(?:`{3,}|~{3,})", re.M)


@needs_node
@pytest.mark.parametrize("sep", ["<code></code>", "<code> </code>", "<b></b>", "<span></span>"])
def test_html2md_tilde_fence_from_fragments(sep):
    body = f"<p>~{sep}~~dataviewjs<br>dv.paragraph(1)<br>~{sep}~~</p>"
    r = subprocess.run(["node", str(HTML2MD), "--base-url", "https://ok.test/"],
                       input=f"<html><body><article>{body}</article></body></html>",
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert not FENCE_LINE.search(r.stdout), r.stdout


def test_slide_deck_tilde_fence_from_parser_chunks():
    slide = "<section><p>~<span></span>~~dataviewjs<br>dv.paragraph(1)<br>~<span></span>~~</p></section>"
    page = f"<html><body><div class=\"reveal\"><div class=\"slides\">{slide * 3}</div></div></body></html>"
    deck = slide_deck.parse_deck(page, url="https://ok.test/d")
    assert deck is not None
    assert not FENCE_LINE.search(deck.markdown), deck.markdown


@pytest.mark.parametrize("parts", [["~", "~~dataviewjs"], ["~~", "~js-engine"], ["- ~", "~~x"]])
def test_joined_escaped_fragments_cannot_open_a_fence(parts):
    assert not FENCE_LINE.search("".join(escape_md_text(p) for p in parts))
