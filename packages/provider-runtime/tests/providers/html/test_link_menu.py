"""#19: a site menu that is not marked up as navigation must not lead the capture.

jpmorgan.com "insights" pages put a mega-menu before the article: link-card
lists (a link plus a one-line blurb each) and "Key Links" columns in plain
<div>/<section>/<ul> blocks. Only the tab strip is a <nav>; the panels sit
beside it, so tag- and role-based chrome removal keeps them. The fixture below
copies that structure (not the page itself). Both content pickers (html2md.mjs
and the plain-text fallback in _athena_fetch_page) are covered.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

HTML2MD = Path(fp.__file__).with_name("html2md.mjs")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="html2md needs node")

MENU_GROUPS = [
    ("For Companies and Institutions",
     ["Commercial Banking", "Global Corporate Banking", "Investment Banking", "Payments"]),
    ("For Institutional Investors",
     ["Asset Management", "Markets", "Securities Services", "Global Research"]),
    ("For Individuals", ["Wealth Management", "Private Bank"]),
    ("For Employers", ["Workplace Solutions"]),
    ("Who We Serve", []),
    ("All Insights", []),
    ("Insights by Topic", []),
    ("Insights by Type", []),
    ("About Us", []),
]
MENU_BLURB = "MENU-BLURB tailored solutions for businesses of every size, from start-ups to global firms."
ARTICLE = [
    ("Key takeaways", "AI is both the primary threat and the primary defense mechanism"),
    ("The identity perimeter has moved", "Enterprise AI adoption is pushing identity"),
    ("How AI is expanding the attack surface: APIs, agents and impersonation",
     "Attackers now automate reconnaissance"),
    ("Where venture capital is concentrating in cybersecurity", "Venture funding patterns reflect"),
    ("How to build for an agent-driven security environment", "Builders should assume agents act"),
    ("What this means for builders and investors", "AI-native solutions to problems that AI itself"),
]


def _panel(group: str, subs: list[str]) -> str:
    cards = "".join(
        f'<li><div class="item"><h3><a href="/{i}">{s}</a></h3>'
        f'<p class="description">{MENU_BLURB}</p></div></li>'
        for i, s in enumerate(subs)
    )
    keylinks = "".join(f'<a href="/k/{group[:3]}{i}">Key link {i} of {group}</a>' for i in range(5))
    return (
        '<section class="primary-navigation-tab-nav-content">'
        f'<div class="content-title-container"><h2 class="title">{group}</h2></div>'
        f'<ul class="cards">{cards}</ul>'
        f'<h3>Key Links</h3><div class="column-links-container">{keylinks}</div></section>'
    )


def _menu() -> str:
    tabs = "".join(f'<li><button role="tab">{g}</button></li>' for g, _ in MENU_GROUPS)
    panels = "".join(_panel(g, s) for g, s in MENU_GROUPS)
    return (
        '<div class="header responsivegrid"><div class="primary-navigation">'
        '<div class="cmp-primary-navigation"><a href="/">J.P. Morgan</a>'
        '<a href="/contact">Contact us</a><a href="/careers">Careers</a></div>'
        '<div class="cmp-expanded-container">'
        f'<nav aria-label="Solutions"><ul role="tablist">{tabs}</ul></nav>'
        f'<div class="primary-navigation-sections">{panels}</div>'
        "</div></div></div>"
    )


def _article() -> str:
    sections = "".join(
        f'<div class="cmp-text"><h2>{h}</h2>'
        f"<p>{lead} across every layer of the stack, and teams respond in kind. </p>"
        + "<p>Supporting paragraph with enough words to look like a real article body. </p>" * 5
        + "</div>"
        for h, lead in ARTICLE
    )
    related = "".join(
        f'<div class="card"><a href="/insights/r{i}">Related story {i}</a>'
        f"<p>{'Summary of the related story with a full sentence of text. ' * 3}</p></div>"
        for i in range(4)
    )
    return (
        '<div class="responsivegrid">'
        '<aside><article class="cmp-article-side-panel-container">'
        '<h1 class="title">Cybersecurity in the age of artificial intelligence</h1>'
        '<p class="date">June 25, 2026</p></article></aside>'
        f"{sections}"
        f'<div class="related-insights"><h2>Related insights</h2>{related}</div></div>'
    )


def jpm_like_page() -> str:
    return (
        "<html><head><title>AI Cybersecurity: Threats, Funding &amp; Builder Priorities</title></head>"
        f'<body><div class="root responsivegrid"><div class="aem-Grid">{_menu()}{_article()}'
        "</div></div></body></html>"
    )


def _html2md(html: str) -> str:
    r = subprocess.run(
        ["node", str(HTML2MD), "--base-url", "https://example.com/insights/x"],
        input=html, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def _assert_article_whole(text: str) -> None:
    for heading, lead in ARTICLE:
        assert heading in text, heading
        assert lead in text, lead


# ── html2md.mjs ──────────────────────────────────────────────────────────────


@needs_node
def test_html2md_drops_an_unmarked_mega_menu_before_the_article():
    md = _html2md(jpm_like_page())
    _assert_article_whole(md)
    assert "Related insights" in md and "Related story 3" in md  # after the article: kept
    for group, subs in MENU_GROUPS:
        assert group not in md, group
        for s in subs:
            assert s not in md, s
    assert "Key Links" not in md
    assert "MENU-BLURB" not in md
    body = md.split("\n", 1)[1]  # past the <title> line
    assert body.lstrip().startswith("## Key takeaways"), body[:200]


@needs_node
def test_html2md_drops_a_div_ul_menu_with_no_nav_or_role():
    menu = "<div class='menu'><ul>" + "".join(
        f"<li><a href='/s{i}'>MENU-ITEM {i}</a></li>" for i in range(12)
    ) + "</ul></div>"
    page = (
        f"<html><body>{menu}<div class='content'><h1>Real title</h1>"
        f"<p>{'Article sentence with real content. ' * 40}</p></div></body></html>"
    )
    md = _html2md(page)
    assert "Real title" in md and "Article sentence" in md
    assert "MENU-ITEM" not in md


@needs_node
def test_html2md_drops_the_menu_when_the_logo_is_an_h1_link():
    menu = "<div class='top'><h1><a href='/'>BRAND-LOGO</a></h1><ul>" + "".join(
        f"<li><a href='/s{i}'>MENU-ITEM {i}</a></li>" for i in range(12)
    ) + "</ul></div>"
    page = (
        f"<html><body>{menu}<div><h1>Real title</h1>"
        f"<p>{'Article sentence with real content. ' * 40}</p></div></body></html>"
    )
    md = _html2md(page)
    assert "Real title" in md and "Article sentence" in md
    assert "MENU-ITEM" not in md and "BRAND-LOGO" not in md


@needs_node
def test_html2md_keeps_a_link_list_after_the_title():
    resources = "<ul>" + "".join(
        f"<li><a href='/r{i}'>RESOURCE {i}</a></li>" for i in range(15)
    ) + "</ul>"
    page = (
        "<html><body><div><h1>Guide</h1>"
        f"<p>{'Intro sentence of the guide. ' * 30}</p><h2>Resources</h2>{resources}"
        "</div></body></html>"
    )
    md = _html2md(page)
    for i in range(15):
        assert f"RESOURCE {i}" in md


@needs_node
def test_html2md_keeps_sparse_or_prose_links_before_the_title():
    crumbs = "<div class='crumbs'><a href='/'>CRUMB-HOME</a><a href='/b'>CRUMB-BLOG</a></div>"
    intro = "<div class='intro'><p>" + "".join(
        f"PROSE-INTRO sentence {i} with a <a href='/p{i}'>cited source</a> and its context. " * 1
        + "More explanation of the point in plain words follows here. "
        for i in range(12)
    ) + "</p></div>"
    page = (
        f"<html><body>{crumbs}{intro}<div><h1>Title</h1>"
        f"<p>{'Body sentence. ' * 60}</p></div></body></html>"
    )
    md = _html2md(page)
    assert "CRUMB-HOME" in md  # too few links to be a menu
    assert "PROSE-INTRO sentence 11" in md  # text, not a link list


@needs_node
def test_html2md_leaves_a_page_without_a_title_heading_alone():
    # No <h1> to say where the content starts (link index pages, some apps):
    # nothing is dropped as a menu.
    links = "<ul>" + "".join(f"<li><a href='/s{i}'>INDEX-LINK {i}</a></li>" for i in range(12)) + "</ul>"
    md = _html2md(f"<html><body><div>{links}</div><p>{'Footer-ish note. ' * 5}</p></body></html>")
    assert "INDEX-LINK 11" in md


# ── plain-text fallback picker (_athena_fetch_page), on a real DOM ──────────


def _launch_kwargs():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return {}
    except Exception:
        return None


_LAUNCH = _launch_kwargs()
needs_chromium = pytest.mark.skipif(_LAUNCH is None, reason="no Chromium available for Playwright")


def _fallback_text(html: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, **_LAUNCH)
        try:
            pg = b.new_page()
            pg.set_content(html)
            text = fp._pick_main_text(pg)
        finally:
            b.close()
    assert text is not None
    return text


@needs_chromium
def test_fallback_picker_drops_an_unmarked_mega_menu():
    text = _fallback_text(jpm_like_page())
    _assert_article_whole(text)
    # Panel content only: the tab strip is a real <nav>, which this picker
    # judges by length but does not remove from the body text.
    assert "MENU-BLURB" not in text
    assert "Key Links" not in text
    assert "Global Corporate Banking" not in text
    assert "Key link 0 of For" not in text


@needs_chromium
def test_fallback_picker_keeps_a_link_list_after_the_title():
    resources = "<ul>" + "".join(f"<li><a href='/r{i}'>RESOURCE {i}</a></li>" for i in range(15)) + "</ul>"
    page = (
        "<html><body><div><h1>Guide</h1>"
        f"<p>{'Intro sentence of the guide. ' * 30}</p>{resources}</div></body></html>"
    )
    text = _fallback_text(page)
    assert "RESOURCE 14" in text
