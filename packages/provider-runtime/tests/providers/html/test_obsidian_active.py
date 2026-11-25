"""#23 (gap): page text must not become content Obsidian runs.

Obsidian with the Dataview plugin runs fenced blocks such as ```dataviewjs (also
~~~ fences, and inside quotes and list items) and inline code that starts
with "=" or "$=" (inline DQL / JavaScript queries, on by default). Page text
can't form either: text backticks are escaped, fence-like lines are
neutralised, and generated inline code that starts with "=" / "$=" is made
inert. Covers html2md.mjs and the Python text routes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from arcus.provider_runtime.providers._shared.markdown_text import escape_md_text
from arcus.provider_runtime.providers.html import _athena_fetch_page as fp
from arcus.provider_runtime.providers.html import x_syndication

HTML2MD = Path(fp.__file__).with_name("html2md.mjs")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="html2md needs node")

BT = "`"
JS = 'dv.el("img", "", {attr: {src: "http://127.0.0.1:9/px"}})'
# A line CommonMark would open a fence on: optional container prefixes
# (quote markers, list markers), then 3+ unescaped backticks or tildes.
FENCE_LINE = re.compile(r"^(?:[ \t>]|[-*+][ \t]|\d{1,9}[.)][ \t])*(?:" + BT + "{3,}|~{3,})", re.M)
# A code span whose content starts with "=" or "$=" (an unescaped backtick run
# followed by optional spaces and the prefix).
INLINE_QUERY = re.compile(r"(?<!\\)(?:\\\\)*" + BT + r"+\s*\$?=")


def _md(body: str) -> str:
    r = subprocess.run(
        ["node", str(HTML2MD), "--base-url", "https://ok.test/p"],
        input=f"<html><body><article>{body}</article></body></html>",
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def _assert_inactive(md: str) -> None:
    assert not FENCE_LINE.search(md), md
    assert not INLINE_QUERY.search(md), md


# ── html2md ──────────────────────────────────────────────────────────────────


@needs_node
@pytest.mark.parametrize("fence", [BT * 3, BT * 4, "~~~", "~~~~"])
@pytest.mark.parametrize("wrap", ["<p>{}</p>", "<blockquote>{}</blockquote>", "<ul><li>{}</li></ul>", "<div>{}</div>"])
def test_text_cannot_open_a_fence(fence, wrap):
    inner = f"{fence}dataviewjs\n{JS}\n{fence}"
    md = _md(wrap.format(inner))
    _assert_inactive(md)
    assert "dataviewjs" in md


@needs_node
@pytest.mark.parametrize("q", [f"$= {JS}", "= this.file.name", " $=x"])
def test_text_cannot_form_an_inline_query(q):
    md = _md(f"<p>see {BT}{q}{BT} here</p>")
    _assert_inactive(md)
    assert "see" in md and "here" in md


@needs_node
@pytest.mark.parametrize("q", [f"$= {JS}", "= this.file.name", "  =x"])
def test_code_element_is_not_an_inline_query(q):
    md = _md(f"<p>see <code>{q}</code> here</p>")
    _assert_inactive(md)


@needs_node
def test_control_ordinary_code_stays_code():
    md = _md(f"<p><code>a = b</code> and <code>x</code></p><pre>{BT * 3}\nfenced\n{BT * 3}</pre>")
    assert f"{BT}a = b{BT}" in md and f"{BT}x{BT}" in md, md
    assert f"{BT * 4}\n{BT * 3}\nfenced\n{BT * 3}\n{BT * 4}" in md, md


@needs_node
@pytest.mark.parametrize("body", ["<p>&lt;% tp.system.prompt() %&gt;</p>", "<p>raw <% tp.file.title %> text</p>",
                                  "<p>&lt;%* await tp.system.clipboard() %&gt;</p>"])
def test_templater_syntax_in_text_is_inert(body):
    md = _md(body)
    assert "<%" not in md, md  # Templater only runs "<% ... %>"
    assert "&lt;%" in md, md


@needs_node
@pytest.mark.parametrize("body", ["<pre>&lt;% tp.file.title %&gt;\n&lt;%* tR += 1 %&gt;</pre>",
                                  "<p><code>&lt;%+ tp.date.now() %&gt;</code></p>",
                                  "<pre><code>erb: &lt;%= @user.name %&gt;</code></pre>"])
def test_templater_syntax_in_code_is_inert(body):
    # Templater reads the raw file, code blocks included, and ignores markdown.
    md = _md(body)
    assert "<%" not in md, md
    assert "&lt;%" in md, md


# ── Python text routes ───────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    f"{BT * 3}dataviewjs\n{JS}\n{BT * 3}",
    f"> ~~~dataviewjs\n> {JS}\n> ~~~",
    f"- {BT * 3}dataview\n- LIST",
    f"see {BT}$= {JS}{BT} here",
    f"see {BT}= this.file.name{BT}",
])
def test_escape_md_text_is_inactive(text):
    _assert_inactive(escape_md_text(text))


@pytest.mark.parametrize("text", ["<% tp.system.prompt() %>", "<%* await tp.file.title %>"])
def test_escape_md_text_makes_templater_inert(text):
    out = escape_md_text(text)
    assert "<%" not in out and "&lt;%" in out, out


def test_plain_text_fallback_is_inactive():
    _assert_inactive(fp._plain_text_to_markdown(f"intro\n~~~dataviewjs\n{JS}\n~~~\nsee {BT}$= x{BT}"))


def test_x_syndication_is_inactive():
    rec = {"text": f"{BT * 3}dataviewjs\n{JS}\n{BT * 3} and {BT}$= x{BT}", "user": {"screen_name": "me"}}
    _assert_inactive(x_syndication.parse(rec, "1")["text"])
