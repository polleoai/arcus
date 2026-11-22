"""slide_deck — reveal.js HTML slide decks → clean, depth-aware per-slide text.

reveal.js renders each slide from a ``<section>`` in the *static* server HTML,
but the page does not look like an article: readability-based extractors (the
generic html2md path) either return empty or leak raw HTML entities
(``&middot;`` / ``&amp;`` / ``&#NN;``) and concatenated headings. The slide text
is fully present in the served HTML, so a section-walker recovers it — no
browser needed (proven against provos.org's embedded ironcurtain.dev decks).

Scope: reveal.js specifically (``class="reveal"`` + >= 3 ``<section>``). Other
slide hosts (Google Slides, SlideShare, slides.com) paint to canvas/JS and are
not statically extractable — out of scope; they fall through.

reveal.js nests vertical slides: ``<section><section>…</section></section>``.
Only LEAF sections (no nested ``<section>``) are real slides; the wrapping
parent is a vertical stack. Because of that nesting a flat regex can't reliably
split slides — this uses a depth-aware ``HTMLParser`` leaf-section walk.

Ported into arcus from athena's ``bin/lib/slide_deck.py`` (shipped as a
temporary athena-side fallback in 1.6.1). Single-URL, vault-unaware extraction
is arcus's domain per the athena/arcus split, so it lives here now; athena drops
its copy once a released arcus extracts decks cleanly.

  looks_like_slide_deck(html)        -> bool
  parse_slides(html)                 -> list[str]        (leaf-slide texts, in order)
  parse_deck(html, url=...)          -> Deck | None      (title + slides + markdown)
  extract_slide_deck(html, url=...)  -> str | None       (markdown, or None if not a deck)
  fetch_html(url)                    -> str | None        (static GET, no browser)
"""

from __future__ import annotations

import html as _html
import re
import urllib.request

from arcus.provider_runtime import url_guard
from dataclasses import dataclass
from html.parser import HTMLParser

_REVEAL_ROOT_RE = re.compile(r'class\s*=\s*["\'][^"\']*\breveal\b', re.IGNORECASE)
_SECTION_OPEN_RE = re.compile(r"<section\b", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_MIN_SLIDES = 3

_SKIP_TAGS = {"script", "style", "aside"}
_LINEBREAK_END = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                  "ul", "ol", "pre", "blockquote", "tr", "figcaption", "section"}


class _DeckParser(HTMLParser):
    """Collect the text of leaf <section>s (one per real slide), in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.slides: list[str] = []
        self._section_depth = 0
        # Per open section: did it contain a nested <section>? (→ not a leaf)
        self._has_child: list[bool] = []
        self._buf: list[str] = []          # text buffer for the innermost section
        self._skip = 0                     # >0 while inside script/style/aside

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag == "section":
            if self._has_child:
                self._has_child[-1] = True   # parent now has a child → not leaf
            # Starting a new innermost section: flush the parent's buffer (it
            # belongs to a non-leaf and is discarded once a child opens).
            if self._section_depth >= 1:
                self._buf = []
            self._section_depth += 1
            self._has_child.append(False)
            self._buf = []
            return
        if self._skip or self._section_depth == 0:
            return
        if tag == "li":
            self._buf.append("\n- ")
        elif tag == "br":
            self._buf.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "section":
            if self._section_depth == 0:
                return
            is_leaf = not self._has_child[-1]
            if is_leaf:
                text = _clean("".join(self._buf))
                if text:
                    self.slides.append(text)
            self._has_child.pop()
            self._section_depth -= 1
            self._buf = []
            return
        if self._skip or self._section_depth == 0:
            return
        if tag in _LINEBREAK_END:
            self._buf.append("\n")

    def handle_data(self, data):
        if self._skip or self._section_depth == 0:
            return
        self._buf.append(data)


def _clean(text: str) -> str:
    """Normalise a slide's accumulated text: trim lines, collapse blank runs."""
    text = _html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def looks_like_slide_deck(html_text: str) -> bool:
    """True when `html_text` is a reveal.js deck worth section-walking."""
    if not html_text:
        return False
    if not _REVEAL_ROOT_RE.search(html_text):
        return False
    return len(_SECTION_OPEN_RE.findall(html_text)) >= _MIN_SLIDES


def deck_title(html_text: str) -> str:
    """Return the page <title> (whitespace-collapsed, entity-unescaped) or ""."""
    m = _TITLE_RE.search(html_text or "")
    if not m:
        return ""
    return _html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()


def parse_slides(html_text: str) -> list[str]:
    """Return the cleaned text of each LEAF slide, in document order.

    Empty list when `html_text` is not a reveal.js deck, or when the depth-aware
    walk recovers fewer than `_MIN_SLIDES` real slides.
    """
    if not looks_like_slide_deck(html_text):
        return []
    parser = _DeckParser()
    try:
        parser.feed(html_text)
    except Exception:
        return []
    slides = [s for s in parser.slides if s]
    return slides if len(slides) >= _MIN_SLIDES else []


@dataclass(frozen=True)
class Deck:
    """A parsed reveal.js deck: title, per-slide leaf texts, and rendered md."""

    title: str
    slides: list[str]
    markdown: str


def parse_deck(html_text: str, url: str = "") -> Deck | None:
    """Return a `Deck` (title + leaf slides + markdown) or None if not a deck."""
    slides = parse_slides(html_text)
    if not slides:
        return None
    title = deck_title(html_text)
    return Deck(title=title, slides=slides, markdown=_render_markdown(title, slides, url))


def _render_markdown(title: str, slides: list[str], url: str) -> str:
    """Render leaf slides as `# title` + `> Slide deck …` + `## Slide N` blocks.

    Output shape (kept identical to athena's slide_deck so downstream markdown
    consumers see no change across the athena→arcus handoff)::

        # <deck title>

        > Slide deck · N slides · <url>

        ## Slide 1
        …text…
    """
    parts: list[str] = []
    if title:
        parts.append(f"# {title}")
    meta = f"> Slide deck · {len(slides)} slides"
    if url:
        meta += f" · {url}"
    parts.append(meta)
    for i, slide in enumerate(slides, 1):
        parts.append(f"## Slide {i}\n\n{slide}")
    return "\n\n".join(parts).strip()


def extract_slide_deck(html_text: str, url: str = "") -> str | None:
    """Return a markdown rendering of a reveal.js deck, or None if not a deck."""
    deck = parse_deck(html_text, url)
    return deck.markdown if deck else None


def fetch_html(url: str, timeout: int = 15) -> str | None:
    """Lightweight GET of `url` returning decoded HTML, or None on any failure.

    Deliberately NOT a browser: reveal.js slide text lives in the static
    server-rendered ``<section>``s (proven against provos.org), so urllib is
    enough and avoids a Playwright spin-up. Soft-fails on every error class so
    deck recovery can never break the surrounding extraction.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            },
        )
        with url_guard.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and ctype:
                return None
            raw = resp.read(5_000_000)  # 5 MB cap — deck HTML is a single page
        charset = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype)
        if m:
            charset = m.group(1)
        return raw.decode(charset, errors="replace")
    except url_guard.UrlRefusedError:
        raise  # a refusal is never a soft failure
    except Exception:
        return None
