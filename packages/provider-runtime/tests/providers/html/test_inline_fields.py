"""#25: no Dataview inline field can come from captured text.

Dataview reads fields from the raw text of every paragraph, heading and list
line, inline code included: "[key:: value]", "(key:: value)" and a line-leading
"key:: value". Markdown escapes don't stop it ("\\[inl:: 2\\]" is still a
field); only the "::" itself does. Every "::" in text becomes ":\\:" (renders
"::"), the rule athena's note_safety uses, so both escapers agree byte for byte.
Inline code can't hold a backslash escape, so its "::" gets a zero-width space;
link targets get "%3A". Fenced code blocks are not read by Dataview and stay as
they are. (The .txt / .md / pdf / docs / youtube / OCR / writer routes are in
tests/test_capture_injection.py.)
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from arcus.provider_runtime.providers._shared import markdown_text as mt
from arcus.provider_runtime.providers.html import _athena_fetch_page as fp
from arcus.provider_runtime.providers.html import slide_deck, x_syndication

HTML2MD = Path(fp.__file__).with_name("html2md.mjs")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="html2md needs node")
BT = "`"
FIELDS = "a [inl:: 2] b (par:: 3) c:::d"


def _md(body: str) -> str:
    r = subprocess.run(["node", str(HTML2MD), "--base-url", "https://ok.test/p"],
                       input=f"<html><body><article>{body}</article></body></html>",
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _outside_fences(md: str) -> str:
    return re.sub(r"(?ms)^(`{3,})[^\n]*\n.*?^\1`*[ \t]*$", "", md)


# ── the shared escapers ─────────────────────────────────────────────────────


def test_escape_md_text_breaks_every_field_separator():
    assert mt.escape_md_text("a [inl:: 2] b") == "a \\[inl:\\: 2\\] b"
    assert mt.escape_md_text("evil:: 1") == "evil:\\: 1"
    assert mt.escape_md_text("(par:: 3)") == "(par:\\: 3)"
    assert mt.escape_md_text("c:::d") == "c:\\:\\:d"
    assert "::" not in mt.escape_md_text(FIELDS)


def test_escape_md_text_matches_athenas_rule():
    # athena (note_safety.py): the bracket escapes, then re.sub(r":(?=:)", r":\\", s)
    for s in (FIELDS, "evil:: 1", "x \\:: y", "::::", "a: b :: c"):
        before = mt.escape_md_text(s.replace(":", "\0")).replace("\0", ":")
        assert mt.escape_md_text(s) == re.sub(r":(?=:)", r":\\", before), s


def test_unescape_md_text_round_trips():
    for s in (FIELDS, "evil:: 1", "x \\:: y", "::::", "a\\:b"):
        assert mt.unescape_md_text(mt.escape_md_text(s)) == s


def test_markdown_url_breaks_field_separators():
    u = mt.markdown_url("https://ok.test/a::b(par::3)")
    assert u and "::" not in u and u.startswith("https://ok.test/a:%3Ab"), u


def test_sanitize_markdown_breaks_fields_outside_code_blocks():
    src = "\n".join(["# T [h:: 1]", FIELDS, "evil:: 1", "- item [li:: 2]", "> quote (q:: 3)",
                     BT + "code:: 4" + BT, "[l](https://ok.test/a::b)", "",
                     BT * 3 + "python", "d = {'k:: v'}", BT * 3])
    out = mt.sanitize_markdown(src)
    assert "::" not in _outside_fences(out), out
    assert BT * 3 + "python\nd = {'k:: v'}\n" + BT * 3 in out, out  # code blocks verbatim


# ── html2md ──────────────────────────────────────────────────────────────────


@needs_node
@pytest.mark.parametrize("wrap", ["<p>{}</p>", "<h2>{}</h2>", "<ul><li>{}</li></ul>", "<blockquote>{}</blockquote>",
                                  "<table><tr><th>h</th></tr><tr><td>{}</td></tr></table>",
                                  "<p><a href='https://ok.test/x'>{}</a></p>", "<p><b>{}</b></p>"])
def test_page_text_has_no_field_separator(wrap):
    md = _md(wrap.format(FIELDS) + "<p>evil:: 1</p>")
    assert "::" not in md, md
    assert "inl:\\: 2" in md, md


@needs_node
def test_page_text_escaping_matches_python():
    for s in (FIELDS, "evil:: 1", "::::"):
        assert _md(f"<p>{s}</p>").strip() == mt.escape_md_text(s)


@needs_node
def test_inline_code_link_and_image_targets_have_no_field_separator():
    md = _md("<p><code>[k:: v] x::y</code> <a href='https://ok.test/a::b'>l</a> "
             "<img src='https://ok.test/i::j.png' alt='(alt:: 1)'></p>")
    assert "::" not in md, md
    assert "k:" + chr(0x200B) + ": v" in md, md


@needs_node
@pytest.mark.parametrize("body", ["<p><span>[k:</span>: v]</p>", "<p>k<span>:</span>: v</p>",
                                  "<p>k:<!-- c -->: v</p>", "<p>(k:<code></code>: v)</p>"])
def test_text_pieces_cannot_join_into_a_field(body):
    # Each text node is escaped on its own; a ":" ending one and a ":"
    # starting the next must not meet as "::" once the tags between are gone.
    md = _md(body)
    assert "::" not in md, md


def test_slide_text_pieces_cannot_join_into_a_field():
    slide = "<section><p>[k<span>:</span>: v] and k:<b></b>: w</p></section>"
    page = ("<html><head><title>t</title></head><body>"
            f"<div class=\"reveal\"><div class=\"slides\">{slide * 3}</div></div></body></html>")
    deck = slide_deck.parse_deck(page, url="https://ok.test/deck")
    assert deck is not None and "::" not in deck.markdown, deck.markdown


@needs_node
def test_code_blocks_keep_their_text():
    md = _md("<pre>d = {'k:: v'}</pre>")
    assert "d = {'k:: v'}" in md, md


# ── Python html routes ───────────────────────────────────────────────────────


def test_python_html_routes_have_no_field_separator():
    assert "::" not in fp._plain_text_to_markdown(FIELDS + "\n\nevil:: 1")
    rec = {"text": FIELDS, "quoted_tweet": {"text": "evil:: 1", "user": {"screen_name": "w::x"}},
           "user": {"screen_name": "me"}}
    out = x_syndication.parse(rec, "1")["text"]
    assert "::" not in out, out
    slide = f"<section><p>{FIELDS}</p></section>"
    page = (f"<html><head><title>t (par:: 3)</title></head><body>"
            f"<div class=\"reveal\"><div class=\"slides\">{slide * 3}</div></div></body></html>")
    deck = slide_deck.parse_deck(page, url="https://ok.test/deck")
    assert deck is not None and "::" not in deck.markdown, deck.markdown


def test_metadata_title_shows_the_field_text_plainly():
    from arcus.provider_runtime.providers.html import html as html_provider
    assert html_provider._derive_title("# a [inl:\\: 2\\] b\n", "slug") == "a [inl:: 2] b"
