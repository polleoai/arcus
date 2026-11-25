"""#24 (security review): an inline Dataview query can't hide behind whitespace.

Code-span content that starts with a line ending or any Unicode space before
"=" / "$=" still runs as a query (CommonMark turns line endings into spaces;
Dataview trims the text), so the guard must look past every kind of
whitespace, not only spaces and tabs.
"""

from __future__ import annotations

import re

import pytest

from arcus.provider_runtime.providers._shared import markdown_text as mt

BT = "`"
# A code span whose content, trimmed, starts with "=" or "$=".
INLINE_QUERY = re.compile(r"(?<!\\)(?:\\\\)*(" + BT + r"+)(?!" + chr(0x200B) + r")\s*\$?=")


@pytest.mark.parametrize("ws", ["\n", "\r\n", "\t\n ", chr(0xA0), chr(0x2003), chr(0x3000), chr(0x2028), " \n" + chr(0xA0)])
@pytest.mark.parametrize("q", ["$= dv.current()", "= this.file.name"])
def test_sanitize_markdown_inline_query_behind_whitespace(ws, q):
    out = mt.sanitize_markdown(f"see {BT}{ws}{q}{BT} here")
    assert not INLINE_QUERY.search(out), repr(out)


@pytest.mark.parametrize("q", ["$= dv.current()", "= this.file.name"])
def test_sanitize_markdown_inline_query_behind_a_byte_order_mark(q):
    # JavaScript's trim() strips U+FEFF; Python's \s does not.
    out = mt.sanitize_markdown(f"see `{chr(0xFEFF)}{q}` and `{chr(0xFEFF)} {chr(0xA0)}{q}`")
    assert not re.search(r"(?<!\\)`+(?!" + chr(0x200B) + r")[\s" + chr(0xFEFF) + r"]*\$?=", out), repr(out)


def test_control_ordinary_inline_code_is_unchanged():
    assert mt.sanitize_markdown(f"use {BT}a = b{BT} and {BT}x{BT}") == f"use {BT}a = b{BT} and {BT}x{BT}"
