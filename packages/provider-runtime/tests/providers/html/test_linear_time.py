"""#22 (gauntlet F-AR-010): html2md converts in time linear in the page size.

Unclosed tags made rules rescan to the end of the page from every open tag:
the pair converters (<a>, <h1>, <b>, <em>, <strong>, <li>, <blockquote>), the
role= chrome scan, chrome removal (<nav>, <header>, <footer>, <aside>, role
regions) and the title lookup. One hostile or broken page stalled a capture
for minutes (an unclosed-<nav> page of 300 KB took 47 s). Each page below
converts in well under a second when the work is linear.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

HTML2MD = Path(fp.__file__).with_name("html2md.mjs")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="html2md needs node")
BUDGET_S = 1.0  # node start-up included; linear code needs well under 0.1 s here


def _convert(html: str) -> tuple[str, float]:
    t = time.monotonic()
    r = subprocess.run(["node", str(HTML2MD), "--base-url", "https://ok.test/a"],
                       input=html, capture_output=True, text=True, timeout=600)
    took = time.monotonic() - t
    assert r.returncode == 0, r.stderr[-500:]
    return r.stdout, took


def _page(inner: str, article: bool = False) -> str:
    return "<html><body>" + (f"<article><p>{inner}</p></article>" if article else inner) + "</body></html>"


CASES = {
    "role-attr": _page("<div role=x " * 6000, article=True),          # ~70 KB
    "link-attr": _page('<a href="x" ' * 1000),                          # ~14 KB
    **{f"pair-{t}": _page(f"<{t}>x" * 50000) for t in ("a", "h1", "b", "em", "strong", "li", "blockquote")},
    **{f"chrome-{t}": _page(f"<{t}>x" * 10000) for t in ("nav", "header", "footer", "aside")},
    "chrome-role": _page("<div role=navigation>x" * 5000),
    "title": "<html><head>" + "<title>x" * 20000 + "</head><body><p>b</p></body></html>",
}


@pytest.mark.parametrize("name", list(CASES))
def test_unclosed_tags_convert_in_linear_time(name):
    _, took = _convert(CASES[name])
    assert took < BUDGET_S, f"{name}: {took:.2f} s for {len(CASES[name]) // 1024} KB"


def test_control_large_well_formed_page_is_fast_and_whole():
    para = '<p>Some text with <a href="/l">a link</a> and <b>bold</b>.</p>'
    md, took = _convert(_page(para * 3000, article=True))  # ~190 KB
    assert took < BUDGET_S
    assert md.count("[a link](https://ok.test/l)") == 3000 and md.count("**bold**") == 3000


def test_unclosed_elements_keep_their_text():
    md, _ = _convert(_page("<ul><li>one<li>two</ul><p>tail <b>bold <a href='/x'>link"))
    for word in ("one", "two", "tail", "bold", "link"):
        assert word in md, md


def test_unclosed_chrome_runs_to_its_parent_like_a_browser():
    # A browser keeps an unclosed <nav> open until its parent closes, so what
    # follows inside that parent is chrome, as in the rendered DOM html2md
    # normally converts; content after the parent stays.
    md, _ = _convert("<html><body><div><nav>menu<p>nav text</p></div>"
                     "<article><h1>T</h1><p>" + "Body text. " * 30 + "</p></article></body></html>")
    assert "Body text." in md and "menu" not in md, md
