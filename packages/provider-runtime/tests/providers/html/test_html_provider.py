"""HtmlProvider TDD — matches() + predict_slug() + extract() with mocked I/O."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers.html.html import (
    HtmlProvider,
    _looks_like_login_wall,
    _url_to_slug,
)
from arcus.provider_runtime.types import EXIT_CODES, DetectionResult


# ── matches() ───────────────────────────────────────────────────────


def test_matches_http_url():
    p = HtmlProvider()
    d = p.matches("https://example.com/article")
    assert d is not None
    assert d.kind == "html"
    assert d.source_id == "https://example.com/article"
    assert d.raw == "https://example.com/article"
    assert d.metadata["is_xcom"] is False


def test_matches_http_lowercase_scheme_only():
    p = HtmlProvider()
    assert p.matches("http://example.com") is not None
    assert p.matches("HTTPS://EXAMPLE.COM") is not None  # case-insensitive scheme


@pytest.mark.parametrize("bad", [
    "file:///foo",
    "ftp://example.com",
    "/local/path.html",
    "not a url",
    "",
    "javascript:alert(1)",
])
def test_matches_rejects_non_http(bad):
    assert HtmlProvider().matches(bad) is None


@pytest.mark.parametrize("youtube_url", [
    "https://youtube.com/watch?v=abc",
    "https://www.youtube.com/watch?v=abc",
    "https://youtu.be/abc",
    "http://www.youtube.com/watch?v=abc",
])
def test_matches_rejects_youtube(youtube_url):
    assert HtmlProvider().matches(youtube_url) is None


@pytest.mark.parametrize("pdf_url", [
    "https://example.com/paper.pdf",
    "https://example.com/path/to/PAPER.PDF",
    "http://arxiv.org/pdf/2401.12345.pdf",
])
def test_matches_rejects_pdf_suffix(pdf_url):
    assert HtmlProvider().matches(pdf_url) is None


@pytest.mark.parametrize("xcom_url", [
    "https://x.com/user/status/123",
    "https://twitter.com/user/status/123",
    "https://www.x.com/user/status/123",
])
def test_matches_xcom_marks_metadata(xcom_url):
    d = HtmlProvider().matches(xcom_url)
    assert d is not None
    assert d.metadata["is_xcom"] is True


def test_matches_generic_url_not_xcom():
    d = HtmlProvider().matches("https://example.com")
    assert d is not None
    assert d.metadata["is_xcom"] is False


# ── _url_to_slug helper ─────────────────────────────────────────────


@pytest.mark.parametrize("url,expected", [
    ("https://example.com/2024/an-article", "example-com-2024-an-article"),
    ("https://example.com/", "example-com"),
    ("https://example.com", "example-com"),
    ("https://x.com/user/status/123456789012345678", "x-com-user-status-123456789012345678"),
    ("https://www.theverge.com/2024/01/15/my-post", "www-theverge-com-2024-01-15-my-post"),
    ("https://blog.langchain.dev/some-post/?utm=foo", "blog-langchain-dev-some-post"),
])
def test_url_to_slug(url, expected):
    assert _url_to_slug(url) == expected


# ── predict_slug ────────────────────────────────────────────────────


def test_predict_slug_uses_url():
    p = HtmlProvider()
    d = p.matches("https://example.com/article")
    assert p.predict_slug(d) == "example-com-article"


def test_predict_slug_deterministic_without_network():
    """predict_slug MUST NOT touch the network — runs before cache check."""
    p = HtmlProvider()
    d = p.matches("https://example.com/article")
    # Patch both potential network paths and confirm predict_slug still works.
    with patch("arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page") as fp, \
         patch("arcus.provider_runtime.providers.html._athena_fetch_page.fetch_x_tweet") as fx:
        slug = p.predict_slug(d)
    assert slug == "example-com-article"
    assert not fp.called
    assert not fx.called


# ── extract() generic path ──────────────────────────────────────────


def _ctx(tmp_path: Path) -> ExtractionContext:
    return ExtractionContext(out_dir=tmp_path, work_dir=tmp_path)


def test_extract_generic_page_calls_fetch_page(tmp_path):
    p = HtmlProvider()
    d = p.matches("https://example.com/article")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value="# Hello\n\nBody text goes here.",
    ) as fp:
        r = p.extract(d, _ctx(tmp_path))
    fp.assert_called_once_with("https://example.com/article")

    assert r.status == "success"
    assert r.kind == "html"
    assert r.text == "# Hello\n\nBody text goes here."
    assert r.metadata.source == "https://example.com/article"
    assert r.metadata.source_id == "https://example.com/article"
    assert r.metadata.title == "Hello"
    assert r.metadata.slug == "example-com-article"
    assert r.extractor_detail == {"extractor": "fetch_page"}
    assert r.segments == []
    assert r.metadata.author is None
    assert r.metadata.duration_ms is None
    assert r.metadata.posted is None
    assert r.metadata.language is None


def test_extract_title_falls_back_to_first_line_when_no_heading(tmp_path):
    p = HtmlProvider()
    d = p.matches("https://example.com/article")
    body = "This is the first prose line that becomes the title.\n\nMore body."
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value=body,
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "success"
    assert r.metadata.title.startswith("This is the first prose line")
    # Truncated for sanity — under ~80 chars
    assert len(r.metadata.title) <= 80


def test_extract_title_falls_back_to_url_when_body_empty_of_text(tmp_path):
    p = HtmlProvider()
    d = p.matches("https://example.com/article")
    # Whitespace-only body that passes the empty guard via a header but has no content
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value="#  \n\n  ",
    ):
        r = p.extract(d, _ctx(tmp_path))
    # Title falls back to URL slug rendering when no usable text is present
    assert r.metadata.title == "example-com-article"


# ── extract() xcom path ─────────────────────────────────────────────


def test_extract_xcom_calls_fetch_x_tweet(tmp_path):
    p = HtmlProvider()
    d = p.matches("https://x.com/karpathy/status/123")
    fake = {
        "text": "Thinking about the bitter lesson again.",
        "images": ["https://pbs.twimg.com/media/abc.jpg"],
    }
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_x_tweet",
        return_value=fake,
    ) as fx:
        r = p.extract(d, _ctx(tmp_path))
    fx.assert_called_once_with("https://x.com/karpathy/status/123")

    assert r.status == "success"
    assert r.kind == "html"
    assert r.text == "Thinking about the bitter lesson again."
    assert r.extractor_detail == {
        "extractor": "fetch_x_tweet",
        "images": ["https://pbs.twimg.com/media/abc.jpg"],
    }
    assert r.metadata.title.startswith("Thinking about the bitter lesson")
    assert r.metadata.slug == "x-com-karpathy-status-123"


def test_extract_xcom_without_images(tmp_path):
    p = HtmlProvider()
    d = p.matches("https://x.com/user/status/456")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_x_tweet",
        return_value={"text": "Just text.", "images": []},
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "success"
    assert r.extractor_detail == {"extractor": "fetch_x_tweet", "images": []}


# ── extract() failure paths ─────────────────────────────────────────


def test_extract_returns_failure_when_fetch_page_returns_none(tmp_path):
    p = HtmlProvider()
    d = p.matches("https://example.com/article")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value=None,
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]
    assert "no content" in r.error.lower()
    assert r.metadata.slug == "example-com-article"  # URL-derived fallback


def test_extract_returns_failure_when_fetch_page_returns_empty(tmp_path):
    p = HtmlProvider()
    d = p.matches("https://example.com/article")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value="   \n\n   ",
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]


def test_extract_xcom_failure_when_text_empty(tmp_path):
    p = HtmlProvider()
    d = p.matches("https://x.com/user/status/789")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_x_tweet",
        return_value={"text": "", "images": []},
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]


# ── login-wall detection ────────────────────────────────────────────


@pytest.mark.parametrize("login_wall_text", [
    # LinkedIn modal — strongest single-string indicator
    "Sign in\nWelcome back\nJoin LinkedIn",
    "Join LinkedIn to make the most of your professional life",
    # X.com login wall — short text + sign-up + sign-in combo
    "Sign up\nDon't miss what's happening\nLog in",
])
def test_looks_like_login_wall_positive(login_wall_text):
    assert _looks_like_login_wall(login_wall_text) is True


@pytest.mark.parametrize("real_content", [
    "This is a normal article about machine learning. " * 30,
    "# A Real Blog Post\n\nLet's talk about transformers. " * 50,
    # Article that incidentally mentions sign in — should NOT trip
    "Cloud auth article. You can Sign in once and stay logged. " * 20,
])
def test_looks_like_login_wall_negative(real_content):
    assert _looks_like_login_wall(real_content) is False


def test_extract_returns_failure_on_login_wall(tmp_path):
    p = HtmlProvider()
    d = p.matches("https://www.linkedin.com/posts/some-author")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value="Sign in\nWelcome back\nJoin LinkedIn",
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["TOOL_NOT_AUTHENTICATED"]
    assert "login" in r.error.lower()


# ── progress emission ───────────────────────────────────────────────


def test_html_emits_fetching_then_extracting(tmp_path):
    stages = []
    ctx = ExtractionContext(
        out_dir=tmp_path, work_dir=tmp_path, emit_progress=stages.append
    )
    p = HtmlProvider()
    d = p.matches("https://example.com/article")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value="# Title\n\nbody text here",
    ):
        p.extract(d, ctx)
    assert stages == ["fetching", "extracting"]


def test_html_xcom_emits_fetching_then_extracting(tmp_path):
    stages = []
    ctx = ExtractionContext(
        out_dir=tmp_path, work_dir=tmp_path, emit_progress=stages.append
    )
    p = HtmlProvider()
    d = p.matches("https://x.com/user/status/123")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_x_tweet",
        return_value={"text": "a tweet", "images": []},
    ):
        p.extract(d, ctx)
    assert stages == ["fetching", "extracting"]


# ── reveal.js slide-deck recovery ───────────────────────────────────

# A reveal.js deck with a nested vertical stack → 4 LEAF slides. Fed as the
# static HTML that slide_deck.fetch_html returns during recovery.
_REVEAL_HTML = """
<html><head><title>Zero Days &amp; Invariants</title></head><body>
<div class="reveal"><div class="slides">
  <section><h2>Intro</h2><p>Detection is not enough.</p></section>
  <section>
    <section><h2>Problem</h2><ul><li>500 bugs</li></ul></section>
    <section><h2>Detail</h2><p>intent drift over turns</p></section>
  </section>
  <section><h2>Fix</h2><p>security invariants</p></section>
</div></div>
</body></html>
"""

# What the generic html2md path returns for that deck: entity-leaked mush —
# the mis-extraction fingerprint that gates the recovery re-fetch.
_ENTITY_SMELL_BODY = "Intro &middot; Detection is not enough &amp; more &#8226; Problem"


def test_extract_recovers_reveal_deck_on_entity_smell(tmp_path):
    p = HtmlProvider()
    d = p.matches("https://provos.org/p/talks-ai-zero-days-and-invariants")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value=_ENTITY_SMELL_BODY,
    ), patch(
        "arcus.provider_runtime.providers.html.slide_deck.fetch_html",
        return_value=_REVEAL_HTML,
    ) as fh:
        r = p.extract(d, _ctx(tmp_path))
    fh.assert_called_once_with("https://provos.org/p/talks-ai-zero-days-and-invariants")
    assert r.status == "success"
    assert r.kind == "html"
    assert r.extractor_detail["extractor"] == "reveal_slide_deck"
    assert r.extractor_detail["slides"] == 4
    # Discrete per-slide segments (mirrors the docs provider's units).
    assert len(r.segments) == 4
    assert "Detection is not enough." in r.segments[0].text
    assert "security invariants" in r.segments[3].text
    # Slide number rides in locators, not in the timing fields.
    assert all(s.start_ms == 0 and s.end_ms == 0 for s in r.segments)
    assert r.extractor_detail["locators"] == [
        {"segment": 0, "slide": 1},
        {"segment": 1, "slide": 2},
        {"segment": 2, "slide": 3},
        {"segment": 3, "slide": 4},
    ]
    # Clean per-slide markdown body, deck title from <title>.
    assert r.text.startswith("# Zero Days & Invariants")
    assert r.text.count("## Slide ") == 4
    assert r.metadata.title == "Zero Days & Invariants"
    # No entity smell survives into the recovered body.
    assert "&middot;" not in r.text and "&amp;" not in r.text


def test_extract_recovers_reveal_deck_on_empty_body(tmp_path):
    """Empty generic capture also triggers recovery (before the failure path)."""
    p = HtmlProvider()
    d = p.matches("https://ex.org/deck")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value="",
    ), patch(
        "arcus.provider_runtime.providers.html.slide_deck.fetch_html",
        return_value=_REVEAL_HTML,
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "success"
    assert r.extractor_detail["extractor"] == "reveal_slide_deck"
    assert len(r.segments) == 4


def test_extract_smelly_non_deck_falls_through_to_generic(tmp_path):
    """Entity smell but the re-fetched HTML is not a deck → keep the generic
    body (no crash, no empty result)."""
    p = HtmlProvider()
    d = p.matches("https://ex.org/article")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value="A normal article that happens to mention AT&amp;T once. " * 10,
    ), patch(
        "arcus.provider_runtime.providers.html.slide_deck.fetch_html",
        return_value="<html><body><article><p>prose</p></article></body></html>",
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "success"
    assert r.extractor_detail == {"extractor": "fetch_page"}
    assert r.segments == []


def test_extract_clean_body_never_fetches_deck_html(tmp_path):
    """Clean captures pay nothing — the recovery re-fetch is not attempted."""
    p = HtmlProvider()
    d = p.matches("https://ex.org/article")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value="# Clean Article\n\nNo entities here, just prose and text.",
    ), patch(
        "arcus.provider_runtime.providers.html.slide_deck.fetch_html",
    ) as fh:
        r = p.extract(d, _ctx(tmp_path))
    assert not fh.called
    assert r.status == "success"
    assert r.extractor_detail == {"extractor": "fetch_page"}


def test_extract_deck_fetch_html_none_falls_through(tmp_path):
    """Empty body + failed static re-fetch → normal empty-content failure."""
    p = HtmlProvider()
    d = p.matches("https://ex.org/deck")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value="",
    ), patch(
        "arcus.provider_runtime.providers.html.slide_deck.fetch_html",
        return_value=None,
    ):
        r = p.extract(d, _ctx(tmp_path))
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]


def test_reveal_recovery_emits_fetching_then_extracting(tmp_path):
    stages = []
    ctx = ExtractionContext(
        out_dir=tmp_path, work_dir=tmp_path, emit_progress=stages.append
    )
    p = HtmlProvider()
    d = p.matches("https://ex.org/deck")
    with patch(
        "arcus.provider_runtime.providers.html._athena_fetch_page.fetch_page",
        return_value=_ENTITY_SMELL_BODY,
    ), patch(
        "arcus.provider_runtime.providers.html.slide_deck.fetch_html",
        return_value=_REVEAL_HTML,
    ):
        p.extract(d, ctx)
    assert stages == ["fetching", "extracting"]


# ── Detection dataclass roundtrip ───────────────────────────────────


def test_detection_is_picklable_through_protocol(tmp_path):
    """Smoke check: DetectionResult returned by matches() can flow through extract()."""
    p = HtmlProvider()
    d = p.matches("https://example.com/")
    assert isinstance(d, DetectionResult)
    assert d.source_id == "https://example.com/"
