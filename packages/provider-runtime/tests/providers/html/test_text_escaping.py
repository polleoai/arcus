"""#23 (gauntlet F-AR-011): text on a captured page must stay text in the note.

Entities are decoded once, in text only; text is escaped for markdown (<, [,
], backslash, and an & that would start an entity), so it can never become
a live tag, link, embed or wikilink. Links and embeds keep only http(s) URLs,
and no attribute value (href, src, alt) or title can break out into markup.
Covers html2md.mjs and the Python text paths of the HTML provider.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from arcus.provider_runtime.providers.html import _athena_fetch_page as fp
from arcus.provider_runtime.providers.html import html as html_provider
from arcus.provider_runtime.providers.html import slide_deck, x_syndication

HTML2MD = Path(fp.__file__).with_name("html2md.mjs")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="html2md needs node")

EVIL = "http://127.0.0.1:9/px"
LIVE_TAG = re.compile(r"<\s*/?\s*(img|iframe|b|a|script|svg|video|audio|object|style|div|p)\b", re.I)
EMBED = re.compile(r"!\[[^\]]*\]\(")
# A real (unescaped) "](" target: the "]" has an even number of backslashes before it.
EVIL_REF = re.compile(r"(?<!\\)(?:\\\\)*\]\(\s*<?\s*http://127\.0\.0\.1")
BT = "`"


def _md(body: str, *, page: str | None = None) -> str:
    html = page if page is not None else f"<html><body><article><p>{body}</p></article></body></html>"
    r = subprocess.run(
        ["node", str(HTML2MD), "--base-url", "https://ok.test/dir/page"],
        input=html, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def _targets(md: str) -> int:
    """Real (unescaped) "](" link/embed targets in md."""
    return len(re.findall(r"(?<!\\)(?:\\\\)*\]\(", md))


def _assert_inert(md: str) -> None:
    assert not LIVE_TAG.search(md), md
    assert not EVIL_REF.search(md), md


# ── html2md: text ────────────────────────────────────────────────────────────


@needs_node
def test_entity_text_is_not_a_tag():
    md = _md(f'&lt;img src="{EVIL}"&gt; shown as text')
    _assert_inert(md)
    assert "&lt;img" in md and "shown as text" in md


@needs_node
def test_entities_are_decoded_once():
    md = _md("&amp;lt;b&amp;gt; literal")  # the page shows "&lt;b&gt;"
    _assert_inert(md)
    assert "&amp;lt;b&amp;gt; literal" in md, md  # renders as "&lt;b&gt;"


@needs_node
def test_raw_less_than_in_text_is_escaped():
    md = _md("a < b and <3 and 5>4")
    assert "a &lt; b" in md and "&lt;3" in md and "5>4" in md, md


@needs_node
@pytest.mark.parametrize("text", [
    f"see ![x]({EVIL}) here",
    f"see [x]({EVIL}) here",
    "see ![[secret.png]] here",
    "see [[Private note]] here",
    f"see [x][r] and [r]: {EVIL}",
])
def test_markdown_syntax_in_text_is_escaped(text):
    md = _md(text)
    assert not EMBED.search(md), md
    assert "![[" not in md and "[[" not in md, md
    assert not re.search(r"(?<!\\)\[", md), md  # every text [ is escaped


@needs_node
def test_backslash_in_text_cannot_unescape():
    md = _md(f"path C:\\dir\\ then \\![x]({EVIL})")
    assert not re.search(r"(?<!\\)(?:\\\\)*!\[", md), md  # no unescaped ![
    assert not re.search(r"(?<!\\)(?:\\\\)*\[x", md), md


@needs_node
def test_plain_ampersands_and_unknown_entities_are_kept():
    md = _md("AT&amp;T and R&amp;D, a &mdash; b")
    assert "AT&T and R&D" in md, md


@needs_node
def test_forged_code_placeholder_in_text_is_ignored():
    md = _md("x \ue0000\ue001 y <code>c</code>")
    assert "x" in md and "y" in md
    assert "\ue000" not in md and "\ue001" not in md


# ── html2md: links and embeds ────────────────────────────────────────────────


@needs_node
@pytest.mark.parametrize("href", [
    "obsidian://open?vault=v&amp;file=secret",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "vbscript:x",
    "data:text/html,hi",
    "mailto:a@ok.test",
    "app://local/x",
])
def test_links_keep_only_http_schemes(href):
    md = _md(f'<a href="{href}">LABEL</a> end')
    assert "LABEL" in md, md
    assert "](" not in md, md


@needs_node
@pytest.mark.parametrize("src", [
    "file:///Users/me/secret.png",
    "obsidian://open?file=x.png",
    "data:image/png;base64,iVBORw0KGgo=",
    "javascript:x",
])
def test_embeds_keep_only_http_schemes(src):
    md = _md(f'before <img alt="a" src="{src}"> after')
    assert "before" in md and "after" in md
    assert not EMBED.search(md), md


@needs_node
def test_href_cannot_break_out_of_the_link():
    md = _md(f'<a href="https://ok.test/x) ![p]({EVIL}">t</a>')
    _assert_inert(md)
    assert _targets(md) == 1, md


@needs_node
def test_alt_cannot_break_out_of_the_embed():
    md = _md(f'<img alt="x]({EVIL}) ![y" src="https://ok.test/i.png">')
    _assert_inert(md)
    assert _targets(md) == 1 and "https://ok.test/i.png" in md, md


@needs_node
def test_tag_inside_a_single_quoted_attribute_is_not_a_tag():
    md = _md(f"""<a title='<img src="{EVIL}">' href="https://ok.test/">t</a>""")
    _assert_inert(md)
    assert "[t](https://ok.test/)" in md, md


@needs_node
def test_single_quoted_href_and_src_are_read():
    md = _md("<a href='/a'>A</a> <img alt='i' src='/i.png'>")
    assert "[A](https://ok.test/a)" in md and "![i](https://ok.test/i.png)" in md, md


@needs_node
def test_attribute_entities_are_decoded_once():
    md = _md('<a href="/s?a=1&amp;b=2">q</a>')
    assert "[q](https://ok.test/s?a=1&b=2)" in md, md


# ── html2md: code ────────────────────────────────────────────────────────────


@needs_node
def test_inline_code_with_backticks_cannot_close_early():
    md = _md(f'<code>a{BT}&lt;img src="{EVIL}"&gt;b</code>')
    assert f'{BT * 2}a{BT}<img src="{EVIL}">b{BT * 2}' in md, md


@needs_node
def test_code_block_with_a_fence_inside_cannot_close_early():
    fence = BT * 3
    md = _md("", page=(
        f'<html><body><article><pre>x\n{fence}\n&lt;img src="{EVIL}"&gt;\n{fence}\ny</pre>'
        "</article></body></html>"))
    assert f"{BT * 4}\nx\n{fence}\n<img" in md and f"y\n{BT * 4}" in md, md


@needs_node
def test_code_keeps_angle_brackets_literally():
    md = _md("", page="<html><body><pre><code>&lt;div&gt;hi&lt;/div&gt; &amp;amp;</code></pre></body></html>")
    assert f"{BT * 3}\n<div>hi</div> &amp;\n{BT * 3}" in md, md


@needs_node
def test_code_inside_a_link_label_stays_code():
    md = _md(f'<a href="https://ok.test/"><code>&lt;img src="{EVIL}"&gt;</code></a>')
    assert f'[{BT}<img src="{EVIL}">{BT}](https://ok.test/)' in md, md


# ── html2md: title ───────────────────────────────────────────────────────────


@needs_node
@pytest.mark.parametrize("title", [f'&lt;img src="{EVIL}"&gt;', f'<img src="{EVIL}">', f"![x]({EVIL})"])
def test_title_is_text(title):
    md = _md("", page=f"<html><head><title>{title}</title></head><body><p>body text</p></body></html>")
    first = md.splitlines()[0]
    assert first.startswith("# "), md
    _assert_inert(first)
    assert not EMBED.search(first), first


# ── html2md: controls ────────────────────────────────────────────────────────


@needs_node
def test_control_real_markup_still_converts():
    md = _md('hello <img alt="a" src="https://ok.test/i.png"> world <a href="/r">rel</a> '
             "<b>bold</b> <em>it</em>")
    assert "hello" in md and "world" in md
    assert "![a](https://ok.test/i.png)" in md
    assert "[rel](https://ok.test/r)" in md
    assert "**bold**" in md and "*it*" in md


# ── Python text paths ────────────────────────────────────────────────────────


def test_plain_text_fallback_is_escaped():
    out = fp._plain_text_to_markdown(f'<img src="{EVIL}"> and ![x]({EVIL}) and [[note]]')
    _assert_inert(out)
    assert not EMBED.search(out) and "[[" not in out, out


def test_x_syndication_text_is_escaped():
    rec = {"text": f"&lt;img src=\"{EVIL}\"&gt; ![x]({EVIL}) &amp;lt;b&amp;gt;",
           "quoted_tweet": {"text": f"![q]({EVIL})", "user": {"screen_name": "who"}},
           "user": {"screen_name": "me"}}
    out = x_syndication.parse(rec, "1")["text"]
    _assert_inert(out)
    assert not EMBED.search(out), out
    assert "&amp;lt;b&amp;gt;" in out, out  # decoded once


def test_slide_deck_text_and_title_are_escaped():
    slide = f"<section><p>&lt;img src=\"{EVIL}\"&gt; ![x]({EVIL}) &amp;lt;b&amp;gt;</p></section>"
    page = (f"<html><head><title>&lt;img src=\"{EVIL}\"&gt;</title></head><body>"
            f"<div class=\"reveal\"><div class=\"slides\">{slide * 3}</div></div></body></html>")
    deck = slide_deck.parse_deck(page, url="https://ok.test/deck")
    assert deck is not None
    _assert_inert(deck.markdown)
    assert not EMBED.search(deck.markdown), deck.markdown
    assert "&amp;lt;b&amp;gt;" in deck.markdown, deck.markdown  # decoded once


def test_metadata_title_is_unescaped_text():
    assert html_provider._derive_title("# a \\[b\\] &lt;c&gt; AT&amp;T\n", "slug") == "a [b] <c> AT&T"
