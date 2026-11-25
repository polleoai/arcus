"""#20: each html2md converter pattern must match only its own element.

The open-tag patterns used to match by prefix (<b ~ <button>/<br>, <i ~
<input>/<ins>, <em ~ <embed>, <li ~ <link>, <a ~ <area>) and attribute names by
suffix (href= inside data-href=, alt=/src= inside data-alt=/data-src=). A wrong
open tag pairs with a later real close tag and stripTags flattens everything
between, so a list before the close loses its bullets.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

HTML2MD = Path(fp.__file__).with_name("html2md.mjs")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="html2md needs node")

LIST = "<ul><li>first item</li><li>second item</li></ul>"


def _html2md(body: str) -> str:
    r = subprocess.run(
        ["node", str(HTML2MD), "--base-url", "https://example.com/dir/page"],
        input=f"<html><body>{body}</body></html>", capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def _assert_list_kept(md: str) -> None:
    lines = [ln.strip() for ln in md.splitlines()]
    assert "- first item" in lines, md
    assert "- second item" in lines, md


# (id, element before the list, real close tag after it, its markdown)
COLLISIONS = [
    ("b~button", "<button>Go</button>", "<b>bold</b>", "**bold**"),
    ("b~br", "line<br>", "<b>bold</b>", "**bold**"),
    ("b~br-with-attrs", "line<br class='x'>", "<b>bold</b>", "**bold**"),
    ("b~blockquote", "<blockquote>quoted</blockquote>", "<b>bold</b>", "**bold**"),
    ("b~bdi", "<bdi>name</bdi>", "<b>bold</b>", "**bold**"),
    ("b~bdo", "<bdo dir='rtl'>name</bdo>", "<b>bold</b>", "**bold**"),
    ("i~input", "<input type='search'>", "<i>it</i>", "*it*"),
    ("i~img-without-src", "<img class='c'>", "<i>it</i>", "*it*"),
    ("i~ins", "<ins>added</ins>", "<i>it</i>", "*it*"),
    ("em~embed", "<embed src='x.swf'>", "<em>it</em>", "*it*"),
    ("strong~custom-element", "<strong-box>box</strong-box>", "<strong>bold</strong>", "**bold**"),
    ("code~custom-element", "<code-block>cb</code-block>", "<code>c</code>", "`c`"),
    ("pre~custom-element", "<pre-wrap>pw</pre-wrap>", "<pre>p</pre>", "```"),
    ("h2~custom-element", "<h2-title>ht</h2-title>", "<h2>Heading</h2>", "## Heading"),
    ("blockquote~custom-element", "<blockquote-x>bx</blockquote-x>", "<blockquote>q</blockquote>", "> q"),
]


@pytest.mark.parametrize("before,close,expected", [c[1:] for c in COLLISIONS],
                         ids=[c[0] for c in COLLISIONS])
def test_open_tag_pattern_matches_only_its_own_element(before, close, expected):
    md = _html2md(f"<div>{before}</div>{LIST}<p>{close}</p>")
    _assert_list_kept(md)
    assert expected in md, md


def test_li_pattern_does_not_match_link():
    md = _html2md("<link rel='stylesheet' href='s.css'><p>para one</p>" + LIST)
    lines = [ln.strip() for ln in md.splitlines()]
    assert "para one" in lines, md  # not glued to the first item
    _assert_list_kept(md)


def test_a_pattern_does_not_match_area():
    md = _html2md('<map><area href="/m" alt="region"></map><p>para kept</p>'
                  '<p><a href="/z">zed</a></p>')
    assert "[zed](https://example.com/z)" in md, md
    assert "](https://example.com/m)" not in md and "](/m)" not in md, md
    assert "para kept" in md


def test_data_href_is_not_href():
    md = _html2md(f'<p><abbr data-href="/m">AB</abbr></p>{LIST}<p><a href="/z">zed</a></p>')
    _assert_list_kept(md)
    assert "[zed](https://example.com/z)" in md, md
    assert "/m)" not in md, md


def test_a_with_only_data_href_is_not_a_link():
    md = _html2md('<p><a data-href="/x" class="js">label</a></p>')
    assert "label" in md and "/x)" not in md, md


def test_a_with_data_href_and_href_links_the_href():
    md = _html2md('<p><a href="/y" data-href="/x">label</a></p>')
    assert "[label](https://example.com/y)" in md, md


def test_img_alt_is_not_data_alt():
    md = _html2md('<p><img alt="real alt" data-alt="other" src="/a.png"></p>')
    assert "![real alt](https://example.com/a.png)" in md, md


def test_img_src_wins_over_data_src():
    md = _html2md('<p><img src="/real.png" data-src="/lazy.png" alt="a"></p>')
    assert "![a](https://example.com/real.png)" in md, md


def test_img_with_only_data_src_uses_it():
    # Lazy-loaded images with no src at all keep their real URL (this used to
    # work only because src= was matched inside data-src=).
    md = _html2md('<p><img data-src="/lazy.png" alt="a"></p>')
    assert "![a](https://example.com/lazy.png)" in md, md


def test_img_data_uri_placeholder_uses_data_src():
    md = _html2md('<p><img src="data:image/gif;base64,R0lGOD" data-src="/lazy.png" alt="a"></p>')
    assert "![a](https://example.com/lazy.png)" in md, md


@pytest.mark.parametrize("tag", ["b", "strong", "i", "em"])
def test_empty_emphasis_leaves_no_markers(tag):
    # jpmorgan.com: "...accelerate the trend since.<b></b></p>" came out as "since.****".
    md = _html2md(f"<p>accelerate the trend since.<{tag}></{tag}></p><p>Next.</p>")
    assert "since." in md
    assert "**" not in md and "since.*" not in md, md


@pytest.mark.parametrize("html,expected", [
    ("<strong>s</strong>", "**s**"),
    ("<b class='x'>s</b>", "**s**"),
    ("<em>e</em>", "*e*"),
    ("<i>e</i>", "*e*"),
    ("<code>c</code>", "`c`"),
    ("<h1>T</h1>", "# T"),
    ("<blockquote>q</blockquote>", "> q"),
    ('<a href="/r">r</a>', "[r](https://example.com/r)"),
    ('<img src="/i.png" alt="i">', "![i](https://example.com/i.png)"),
])
def test_converters_still_convert_their_own_elements(html, expected):
    md = _html2md(f"<div>{html}</div>{LIST}")
    _assert_list_kept(md)
    assert expected in md, md
