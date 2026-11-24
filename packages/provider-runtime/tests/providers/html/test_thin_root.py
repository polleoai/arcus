"""#10: content-root choice must not keep a small teaser and drop the article body.

Adobe AEM "insights" pages put a dense hero/teaser in the page's only <article>
and the real body in sibling containers after it; blog index pages put one
featured post in the first <article>. Picking that element alone loses most of
the page. Both content pickers (html2md.mjs and the plain-text fallback in
_athena_fetch_page) are covered.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

HTML2MD = Path(fp.__file__).with_name("html2md.mjs")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="html2md needs node")

SECTIONS = [
    ("The identity perimeter has moved", "Enterprise AI adoption is pushing identity"),
    ("How AI is expanding the attack surface", "Attackers now automate reconnaissance"),
    ("Where venture capital is concentrating", "Venture funding patterns reflect"),
    ("How to build for an agent-driven security environment", "Builders should assume agents act"),
    ("What this means for builders and investors", "AI-native solutions to problems that AI itself"),
]


def _aem_page() -> str:
    body = "".join(
        f'<div class="cmp-text aem-GridColumn"><h2>{h}</h2>'
        + f"<p>{lead} across every layer of the stack, and teams respond in kind. </p>"
        + "<p>Supporting paragraph with enough words to look like a real article body. </p>" * 6
        + "</div>"
        for h, lead in SECTIONS
    )
    return (
        "<html><head><title>AI and cybersecurity</title></head><body>"
        "<nav><a href='/a'>Banking</a><a href='/b'>Markets</a></nav>"
        '<article class="cmp-article-side-panel-container">'
        "<h1>AI, cybersecurity threats and builder priorities</h1><p>November 2026</p>"
        "<p>Overview of where security spending goes next.</p>"
        "<ul><li><a href='#s1'>The identity perimeter</a></li><li><a href='#s2'>Attack surface</a></li></ul>"
        "</article>"
        f'<div class="aem-Grid">{body}</div>'
        "<footer>Copyright footer text</footer></body></html>"
    )


def _html2md(html: str) -> str:
    r = subprocess.run(
        ["node", str(HTML2MD), "--base-url", "https://example.com/insights/x"],
        input=html, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


@needs_node
def test_html2md_keeps_aem_body_after_teaser_article():
    md = _html2md(_aem_page())
    for heading, lead in SECTIONS:
        assert heading in md
        assert lead in md
    assert "AI, cybersecurity threats and builder priorities" in md  # the hero stays
    assert "Copyright footer text" not in md  # chrome is still dropped
    assert len(md.split()) > 400


@needs_node
def test_html2md_keeps_every_post_on_an_index_page():
    posts = "".join(
        f"<article><h3>Post {i}</h3><p>{'Summary sentence of the post. ' * 12}</p></article>"
        for i in range(1, 9)
    )
    md = _html2md(f"<html><body><h1>Blog</h1>{posts}</body></html>")
    for i in range(1, 9):
        assert f"Post {i}" in md


@needs_node
def test_html2md_still_prefers_a_real_article_over_page_chrome():
    article = "<p>" + "Main article sentence with real content. " * 60 + "</p>"
    page = (
        "<html><body><div class='sidebar'><a href='/x'>SIDEBAR-LINK one</a>"
        "<a href='/y'>SIDEBAR-LINK two</a></div>"
        f"<article><h1>Real article</h1>{article}</article>"
        "<div class='related'>RELATED-TEASER text</div></body></html>"
    )
    md = _html2md(page)
    assert "Real article" in md and "Main article sentence" in md
    assert "SIDEBAR-LINK" not in md
    assert "RELATED-TEASER" not in md


@needs_node
def test_html2md_main_is_not_cut_at_a_nested_article_close():
    page = (
        "<html><body><main><h1>Main</h1>"
        f"<article><p>{'Inner article text. ' * 40}</p></article>"
        f"<p>Text after the nested article. {'More main content. ' * 40}</p>"
        "</main></body></html>"
    )
    md = _html2md(page)
    assert "Text after the nested article." in md


@needs_node
def test_html2md_article_is_not_cut_at_a_nested_article_close():
    page = (
        "<html><body><article><h1>Outer</h1>"
        f"<p>{'Outer intro text. ' * 40}</p>"
        "<article><p>Embedded card.</p></article>"
        f"<p>Outer conclusion after the card. {'More outer text. ' * 40}</p>"
        "</article></body></html>"
    )
    md = _html2md(page)
    assert "Outer conclusion after the card." in md


@needs_node
def test_html2md_removes_nested_navigation_whole():
    nav = (
        "<nav class='primary'><a href='/a'>MENU-TOP</a>"
        "<div><nav class='sub'><a href='/b'>MENU-SUB</a></nav>"
        "<section><button>MENU-AFTER-INNER-NAV</button></section></div></nav>"
    )
    md = _html2md(f"<html><body>{nav}<article><h1>A</h1><p>{'Body text. ' * 50}</p></article></body></html>")
    assert "Body text." in md
    assert "MENU-" not in md


@needs_node
def test_html2md_removes_aria_chrome_and_dialogs():
    page = (
        "<html><body>"
        "<div role='navigation'><div>ROLE-NAV <div>deep</div></div></div>"
        "<div class='consent' role='dialog' aria-modal='true'><p>DIALOG-TEXT</p><div>more</div></div>"
        f"<article><h1>A</h1><p>{'Body text. ' * 50}</p></article>"
        "<div role=contentinfo>ROLE-FOOTER</div>"
        "</body></html>"
    )
    md = _html2md(page)
    assert "Body text." in md
    for chrome in ("ROLE-NAV", "DIALOG-TEXT", "ROLE-FOOTER"):
        assert chrome not in md


# ── plain-text fallback picker (_athena_fetch_page) ─────────────────────────


class _El:
    def __init__(self, text):
        self._t = text

    def inner_text(self):
        return self._t


class _Page:
    def __init__(self, by_selector, body):
        self._s, self._body = by_selector, body

    def query_selector(self, sel):
        t = self._s.get(sel)
        return _El(t) if t is not None else None

    def inner_text(self, sel):
        assert sel == "body"
        return self._body


def test_fallback_picker_rejects_a_thin_article():
    teaser = "AI, cybersecurity threats and builder priorities. Overview of where spending goes. " * 3
    assert len(teaser) > 200  # past the 100-char floor: only the thin-root rule rejects it
    body = teaser + "\n" + "\n".join(f"{h}\n{lead} across every layer. " * 6 for h, lead in SECTIONS)
    text = fp._pick_main_text(_Page({"article": teaser}, body))
    assert text is not None
    assert "Enterprise AI adoption is pushing identity" in text


def test_fallback_picker_keeps_a_real_article():
    article = "Main article sentence with real content. " * 60
    body = "SIDEBAR-LINK one\nSIDEBAR-LINK two\n" + article
    text = fp._pick_main_text(_Page({"article": article}, body))
    assert text is not None and "SIDEBAR-LINK" not in text


def test_fallback_picker_returns_none_for_an_empty_page():
    assert fp._pick_main_text(_Page({}, "   ")) is None


# ── review round (0.9.0) ────────────────────────────────────────────────────


@needs_node
def test_html2md_stays_fast_on_pages_with_many_style_blocks():
    import time

    rule = "{color:red;margin:0;padding:0;border:0;font-size:12px;line-height:1.4}" * 5
    styles = "".join(f"<style>.c{i}{rule}</style><div>cell {i}</div>" for i in range(6000))
    assert len(styles) > 2_000_000  # the size of a large rendered page
    page = f"<html><body>{styles}<article><p>{'Body text. ' * 200}</p></article></body></html>"
    t = time.monotonic()
    md = _html2md(page)
    assert time.monotonic() - t < 5
    assert "Body text." in md


@needs_node
def test_html2md_keeps_hyphenated_custom_elements():
    page = (
        "<html><body><article><header-hero><h1>HERO-TITLE</h1></header-hero>"
        "<style-guide><p>STYLE-GUIDE-TEXT</p></style-guide>"
        "<nav-card><p>NAV-CARD-TEXT</p></nav-card>"
        f"<p>{'Body text. ' * 30}</p></article></body></html>"
    )
    md = _html2md(page)
    for kept in ("HERO-TITLE", "STYLE-GUIDE-TEXT", "NAV-CARD-TEXT", "Body text."):
        assert kept in md


@needs_node
def test_html2md_does_not_take_main_menu_for_main():
    page = (
        "<html><body><main-menu><a href='/a'>MENU-ITEM</a></main-menu>"
        f"<article><p>{'Short article text. ' * 20}</p></article></body></html>"
    )
    md = _html2md(page)
    assert "Short article text." in md


@needs_node
def test_html2md_keeps_data_role_elements():
    page = (
        "<html><body><article>"
        f"<div data-role='dialog'><p>DATA-ROLE-TEXT</p></div><p>{'Body text. ' * 30}</p>"
        "</article></body></html>"
    )
    assert "DATA-ROLE-TEXT" in _html2md(page)


class _ChromePage(_Page):
    """Also answers the chrome-text measurement _pick_main_text asks the browser for."""

    def __init__(self, by_selector, body, chrome_chars):
        super().__init__(by_selector, body)
        self._chrome = chrome_chars

    def evaluate(self, script):
        return self._chrome


def test_fallback_picker_ignores_menu_text_when_judging_thinness():
    menus = "Menu entry with a long label\n" * 160  # ~4,500 chars of navigation
    article = "Main article sentence with real content. " * 41  # ~1,700 chars
    page = _ChromePage({"article": article}, menus + article, chrome_chars=len(menus))
    text = fp._pick_main_text(page)
    assert text is not None and "Main article sentence" in text
    assert "Menu entry" not in text
