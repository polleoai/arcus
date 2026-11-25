"""Page text -> markdown text that shows exactly that text (#23).

Text taken from a page (or a post, or a slide) must never become markup in the
note: "<img src=...>" or "![x](url)" shown as text on the page must not turn
into a live tag, embed, link or [[wikilink]] that Obsidian renders, and
fetches, when the note is opened. Mirrors escapeText in html2md.mjs.
"""

from __future__ import annotations

import re

_ENTITY_START = re.compile(r"&(?=#?[A-Za-z0-9]+;)")
_UNESCAPE = re.compile(r"\\([\\\[\]])|&(lt|gt|amp);")
_UNESCAPED = {"lt": "<", "gt": ">", "amp": "&"}


def escape_md_text(s: str) -> str:
    """Decoded text -> markdown showing that text.

    Escapes "\\" first (so no text backslash can undo an escape), "&" where it
    would start an entity, "<" (no tags, autolinks or HTML blocks), and "[" /
    "]" (no links, embeds, reference links or wikilinks). ">" stays: without
    "<" it starts no markup (at most a quote at the start of a line).
    """
    s = s.replace("\\", "\\\\")
    s = _ENTITY_START.sub("&amp;", s)
    return s.replace("<", "&lt;").replace("[", "\\[").replace("]", "\\]")


def unescape_md_text(s: str) -> str:
    """Undo escape_md_text, for plain-text uses such as a metadata title."""
    return _UNESCAPE.sub(lambda m: m.group(1) or _UNESCAPED[m.group(2)], s)


def markdown_url(u: str | None) -> str | None:
    """An http(s) URL made safe inside a markdown link target, or None."""
    if not u or not re.match(r"https?://[^\s/?#]", u, re.I):
        return None
    return re.sub(r"[\s()<>]", lambda m: "%%%02X" % ord(m.group(0)), u)
