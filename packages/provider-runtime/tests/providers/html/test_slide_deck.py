"""slide_deck — reveal.js HTML deck → clean, depth-aware per-slide text.

Ported/adapted from athena's tests/test_slide_deck.py, plus arcus-specific
coverage for the structured parse_deck / parse_slides surface the html provider
uses to build per-slide segments, and an explicit nested-vertical-slide case
proving the depth-aware walk keeps inner slides a flat regex would drop.
"""

from __future__ import annotations

from arcus.provider_runtime.providers.html import slide_deck as sd

# 4 LEAF slides: one wrapping vertical-stack <section> holds two inner slides.
REVEAL = """
<html><head><title>My Talk | Conf</title></head><body>
<div class="reveal"><div class="slides">
  <section><h2>Intro</h2><p>Detection is not enough.</p></section>
  <section>
    <section><h2>Problem</h2><ul><li>500 bugs</li><li>40k instances</li></ul></section>
    <section><h2>Detail</h2><p>intent drift over turns</p></section>
  </section>
  <section><h2>Fix</h2><p>security invariants</p></section>
</div></div>
</body></html>
"""

# Deeper nesting: a vertical stack of THREE inner slides between two horizontal
# slides. Leaf slides = Top A, V1, V2, V3, Top B (5). The wrapping stack
# <section> is NOT a slide. A flat `<section>…</section>` regex mis-splits on
# the nested opens and loses inner slides — the anchor case for depth-awareness.
NESTED = """
<html><head><title>Nested Deck</title></head><body>
<div class="reveal"><div class="slides">
  <section><h2>Top A</h2><p>alpha</p></section>
  <section>
    <section><h2>V1</h2><p>vertical one</p></section>
    <section><h2>V2</h2><p>vertical two</p></section>
    <section><h2>V3</h2><p>vertical three</p></section>
  </section>
  <section><h2>Top B</h2><p>omega</p></section>
</div></div>
</body></html>
"""


# ── looks_like_slide_deck ───────────────────────────────────────────


def test_reveal_with_enough_sections():
    assert sd.looks_like_slide_deck(REVEAL) is True


def test_plain_article_is_not_a_deck():
    html = "<html><body><article><section>a</section></article></body></html>"
    assert sd.looks_like_slide_deck(html) is False


def test_reveal_but_too_few_sections():
    html = '<div class="reveal"><section>only one</section></div>'
    assert sd.looks_like_slide_deck(html) is False


def test_empty():
    assert sd.looks_like_slide_deck("") is False


# ── extract_slide_deck (markdown, athena-compatible shape) ──────────


def test_extracts_title_and_leaf_slides():
    md = sd.extract_slide_deck(REVEAL, url="https://ex.org/talk/")
    assert md is not None
    assert md.startswith("# My Talk | Conf")
    assert "> Slide deck · 4 slides · https://ex.org/talk/" in md
    # 4 LEAF slides (the wrapping vertical-stack <section> is not counted).
    assert md.count("## Slide ") == 4
    assert "Detection is not enough." in md
    assert "- 500 bugs" in md
    assert "security invariants" in md


def test_non_deck_returns_none():
    assert sd.extract_slide_deck("<article><p>just prose</p></article>") is None


def test_slides_in_document_order():
    md = sd.extract_slide_deck(REVEAL)
    assert md.index("Detection is not enough") < md.index("500 bugs")
    assert md.index("500 bugs") < md.index("security invariants")


def test_markdown_without_url_omits_url_suffix():
    md = sd.extract_slide_deck(REVEAL)
    assert "> Slide deck · 4 slides" in md
    assert " · https://" not in md


# ── parse_slides / parse_deck (structured surface for segments) ─────


def test_parse_slides_returns_leaf_texts_in_order():
    slides = sd.parse_slides(REVEAL)
    assert len(slides) == 4
    assert "Detection is not enough." in slides[0]
    assert "500 bugs" in slides[1]
    assert "intent drift over turns" in slides[2]
    assert "security invariants" in slides[3]


def test_parse_slides_non_deck_is_empty():
    assert sd.parse_slides("<article><p>prose</p></article>") == []


def test_parse_deck_bundles_title_slides_and_markdown():
    deck = sd.parse_deck(REVEAL, url="https://ex.org/talk/")
    assert deck is not None
    assert deck.title == "My Talk | Conf"
    assert len(deck.slides) == 4
    assert deck.markdown.startswith("# My Talk | Conf")


def test_parse_deck_non_deck_returns_none():
    assert sd.parse_deck("<article><p>prose</p></article>") is None


def test_deck_title_extraction():
    assert sd.deck_title("<title> My  Talk | Conf </title>") == "My Talk | Conf"
    assert sd.deck_title("<html><body>no title</body></html>") == ""


# ── depth-aware nested vertical slides (anchor: flat regex loses these) ──


def test_nested_vertical_slides_kept_depth_aware():
    """The wrapping vertical-stack <section> is NOT a slide; its three inner
    vertical <section>s ARE, each a distinct slide. A flat regex splitting on
    <section> would either merge the verticals or emit the empty stack — this
    proves the HTMLParser leaf-walk keeps every inner slide, in order."""
    slides = sd.parse_slides(NESTED)
    # 5 leaves: Top A, V1, V2, V3, Top B — the stack wrapper contributes none.
    assert len(slides) == 5
    joined = "\n---\n".join(slides)
    for marker in ("alpha", "vertical one", "vertical two", "vertical three", "omega"):
        assert marker in joined
    # Each vertical slide is its own segment, not merged into one blob.
    v1 = next(s for s in slides if "vertical one" in s)
    v2 = next(s for s in slides if "vertical two" in s)
    v3 = next(s for s in slides if "vertical three" in s)
    assert v1 != v2 != v3
    assert "vertical two" not in v1  # not merged with its siblings
    # Document order preserved across the horizontal/vertical boundary.
    order = [i for i, s in enumerate(slides)
             for m in ("alpha", "vertical one", "vertical two", "vertical three", "omega")
             if m in s]
    assert order == sorted(order)


def test_nested_deck_markdown_numbers_all_leaf_slides():
    md = sd.extract_slide_deck(NESTED, url="https://ex.org/nested/")
    assert md.count("## Slide ") == 5
    assert "> Slide deck · 5 slides · https://ex.org/nested/" in md
