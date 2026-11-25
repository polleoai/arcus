"""Page text -> markdown text that shows exactly that text (#23).

Text taken from a page (or a post, or a slide) must never become markup in the
note: "<img src=...>" or "![x](url)" shown as text on the page must not turn
into a live tag, embed, link or [[wikilink]] that Obsidian renders, and
fetches, when the note is opened. Mirrors escapeText in html2md.mjs.
"""

from __future__ import annotations

import re

_ENTITY_START = re.compile(r"&(?=#?[A-Za-z0-9]+;)")
_UNESCAPE = re.compile(r"\\([\\\[\]`~])|&(lt|gt|amp);")
# A line that would open a fenced block (see escape_md_text).
_FENCE_LINE = re.compile(r"^((?:[ \t>]|[-*+][ \t]|\d{1,9}[.)][ \t])*)(`{3,}|~{3,})", re.M)
_UNESCAPED = {"lt": "<", "gt": ">", "amp": "&"}


def escape_md_text(s: str) -> str:
    """Decoded text -> markdown showing that text.

    Escapes "\\" first (so no text backslash can undo an escape), "`" (no code
    spans, so no Dataview inline queries, and no backtick fences), "&" where it
    would start an entity, "<" (no tags, autolinks, HTML blocks or Templater
    "<% %>"), and "[" / "]" (no links, embeds, reference links or wikilinks).
    ">" stays: without "<" it starts no markup (at most a quote at the start
    of a line). "~" too, so no tilde fence can form even when escaped pieces
    are joined later (parser data chunks, tweet items). A line that would
    still open a fence gets its run escaped as a second line of defence.
    """
    s = s.replace("\\", "\\\\").replace("`", "\\`").replace("~", "\\~")
    s = _ENTITY_START.sub("&amp;", s)
    s = s.replace("<", "&lt;").replace("[", "\\[").replace("]", "\\]")
    return _FENCE_LINE.sub(r"\1\\\2", s)


def unescape_md_text(s: str) -> str:
    """Undo escape_md_text, for plain-text uses such as a metadata title."""
    return _UNESCAPE.sub(lambda m: m.group(1) or _UNESCAPED[m.group(2)], s)


def markdown_url(u: str | None) -> str | None:
    """An http(s) URL made safe inside a markdown link target, or None."""
    if not u or not re.match(r"https?://[^\s/?#]", u, re.I):
        return None
    return re.sub(r"[\s()<>]", lambda m: "%%%02X" % ord(m.group(0)), u)


# ── whole documents (#24) ────────────────────────────────────────────────────

def _is_figure_token(line: str) -> bool:
    # arcus's own figure markers (random per process, so a document can't forge
    # one): kept as they are for place_figures to replace with an embed.
    from arcus.provider_runtime.providers._shared import figures
    return bool(figures._TOKEN_RE.match(line)) or line.strip() == figures.PLACEHOLDER


def to_markdown_text(text: str) -> str:
    """Plain text from a captured source -> markdown that shows that text.

    Every line is escaped (escape_md_text) except arcus's figure-token lines.
    Line structure is kept, so a converter's own headings, lists and tables
    (#, -, |, **) still read as such; anything else that would be markup
    (tags, links, embeds, wikilinks, code, fences, inline queries, Templater)
    is text.
    """
    return "\n".join(ln if _is_figure_token(ln) else escape_md_text(ln) for ln in text.split("\n"))


_PANDOC_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_HTML_BREAK = re.compile(r"<br\s*/?>", re.I)
_DOCLING_ENTITY = re.compile(r"&(amp|lt|gt);")


def decode_tool_markdown(text: str, tool: str) -> str:
    """Undo a converter's own escaping, so its output is decoded exactly once.

    pandoc backslash-escapes markdown punctuation in text; docling escapes
    underscores and HTML (&lt; &gt; &amp;); pymupdf4llm writes in-cell line
    breaks as <br>. Other tiers (pdftotext, python-docx/pptx/openpyxl, zipfile,
    OCR, captions, transcripts) emit plain text.
    """
    if tool == "pandoc":
        return _PANDOC_ESCAPE.sub(r"\1", text)
    if tool == "docling":
        text = text.replace("\\_", "_")
        return _DOCLING_ENTITY.sub(lambda m: _UNESCAPED[m.group(1)], text)
    if tool == "pymupdf4llm":
        return _HTML_BREAK.sub(" ", text)
    return text


def tool_output_to_markdown(text: str, tool: str) -> str:
    """A non-markdown source's converted text -> the note's markdown (policy 2)."""
    return to_markdown_text(decode_tool_markdown(text, tool))


# ── markdown sources (#24, policy 3 and 4) ───────────────────────────────────

ZERO_WIDTH_SPACE = chr(0x200B)
# Fence info strings kept on a code block from a captured .md: plain languages
# only. Anything else (dataview, dataviewjs, js-engine, query, tasks, button,
# run-js, ...) may be code an Obsidian plugin runs, so the block keeps no info.
SAFE_FENCE_LANGS = frozenset("""
bash c c# c++ clojure cmake console cpp cs csharp css csv dart diff dockerfile elixir erb erlang
go graphql groovy haskell hcl html ini java javascript js json json5 jsonc jsx julia kotlin latex
less lua makefile markdown md nginx objc ocaml perl php plaintext powershell proto ps1 python py
r rb ruby rust sass scala scss sh shell sql svelte swift tex text toml ts tsx txt typescript vue
xml yaml yml zsh
""".split())
_COLUMN0_FENCE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_UNESC = r"(?<!\\)((?:\\\\)*)"
_ESCAPED_LT = re.compile(_UNESC + r"\\<")
_PAIR = re.compile(r"([\[\]])(?=[\[\]])")
# \s*+ is possessive: it can't give a space back to let the lookahead fail.
_LINK_TARGET = re.compile(_UNESC + r"\](?=\(\s*+(?!https?://))", re.I)
_REF_DEF = re.compile(_UNESC + r"\](?=:\s*+(?!https?://))", re.I)
_INLINE_QUERY = re.compile(_UNESC + r"(`+)(?=[ \t]*\$?=)")


def _sanitize_prose(text: str) -> str:
    # No raw HTML, autolinks or Templater: "\<" is a literal "<", so both
    # become "&lt;" (Templater ignores markdown escapes).
    text = _ESCAPED_LT.sub(r"\1&lt;", text).replace("<", "&lt;")
    # No [[ or ]] left anywhere in the raw text: no wikilink or embed.
    text = _PAIR.sub(r"\1\\", text)
    # Links, images and reference definitions only to http(s): any other
    # target (file:, obsidian:, javascript:, relative paths, ...) has its "]"
    # escaped, so it is no link at all.
    text = _LINK_TARGET.sub(r"\1\\]", text)
    text = _REF_DEF.sub(r"\1\\]", text)
    # Inline code starting with "=" / "$=" is a Dataview query.
    text = _INLINE_QUERY.sub(lambda m: m.group(1) + m.group(2) + ZERO_WIDTH_SPACE, text)
    # A fence that doesn't open at column 0 (inside a quote or list item, or
    # indented) is not kept as code: it becomes text.
    return _FENCE_LINE.sub(r"\1\\\2", text)


def _frontmatter_to_block(md: str) -> str:
    # The source's own YAML frontmatter is content now (the note has its
    # own): shown as a yaml code block.
    if not md.startswith("---\n"):
        return md
    end = md.find("\n---", 4)
    if end == -1 or md[end + 4:end + 5] not in ("", "\n"):
        return md
    return "```yaml\n" + md[4:end + 1] + "```" + md[end + 4:]


def sanitize_markdown(md: str) -> str:
    """A captured markdown source -> markdown that keeps its structure but can't
    run or pull in anything (policy 3 and 4).

    A fenced block is kept verbatim only when it opens and closes at column 0,
    where no container can end it early; its info string is kept only for a
    plain language, and "<%" in it becomes "&lt;%" (Templater reads raw text).
    Everything else is prose (_sanitize_prose). Inline and indented code are
    prose too, so a "<" in them shows as "&lt;": the accepted cost of not
    guessing code spans the way a renderer might not.
    """
    lines = _frontmatter_to_block(md.replace("\r\n", "\n")).split("\n")
    out: list[str] = []
    prose: list[str] = []

    def flush() -> None:
        if prose:
            out.append(_sanitize_prose("\n".join(prose)))
            prose.clear()

    i, n = 0, len(lines)
    while i < n:
        m = _COLUMN0_FENCE.match(lines[i])
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            flush()
            run, info = m.group(1), m.group(2).strip()
            lang = info.split()[0].lower() if info else ""
            out.append(run + (lang if lang in SAFE_FENCE_LANGS else ""))
            close = re.compile(r"^ {0,3}" + re.escape(run[0]) + "{" + str(len(run)) + r",}[ \t]*$")
            i += 1
            while i < n and not close.match(lines[i]):
                out.append(lines[i].replace("<%", "&lt;%"))
                i += 1
            if i < n:
                out.append(lines[i])
                i += 1
            continue
        prose.append(lines[i])
        i += 1
    flush()
    return "\n".join(out)
