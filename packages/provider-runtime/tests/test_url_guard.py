"""Caller URL guard: every fetch path re-checks redirects and sub-requests.

Each test runs two local servers. PUBLIC serves pages that redirect / embed /
navigate to INTERNAL; the guard refuses INTERNAL's port. The core assertion
is always the same: INTERNAL records zero hits.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
from pathlib import Path

import pytest

from arcus.provider_runtime import url_guard as ug
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.url_guard import UrlRefusedError

LONG = "<p>" + ("Readable article sentence number one. " * 30) + "</p>"


class _Server:
    def __init__(self, routes=None):
        self.hits: list[str] = []
        routes = routes or {}
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _serve(self, body=True):
                outer.hits.append(self.path)
                route = routes.get(self.path.split("?")[0])
                if callable(route):
                    route = route(self)
                code, headers, payload = route or (200, {"Content-Type": "text/html"}, b"<p>x</p>")
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if body:
                    self.wfile.write(payload)

            def do_GET(self):
                self._serve()

            def do_HEAD(self):
                self._serve(body=False)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _html(body: str):
    return (200, {"Content-Type": "text/html"}, body.encode())


def _redirect(to: str, code: int = 302):
    return (code, {"Location": to}, b"")


@pytest.fixture
def internal():
    s = _Server()
    yield s
    s.close()


@pytest.fixture
def guard(internal):
    def _g(url: str) -> None:
        if f":{internal.port}" in url:
            raise PermissionError("internal address")

    with ug.using_url_guard(_g):
        yield _g


def _public(routes):
    return _Server(routes)


def _ctx(tmp_path: Path) -> ExtractionContext:
    return ExtractionContext(out_dir=tmp_path, work_dir=tmp_path)


# ── guard API ───────────────────────────────────────────────────────────────

def test_no_guard_is_backward_compatible(internal):
    """Without a guard, redirects are followed exactly as before."""
    pub = _public({"/r": _redirect(f"{internal.base}/t")})
    try:
        with ug.urlopen(f"{pub.base}/r", timeout=5) as r:
            r.read()
        assert internal.hits == ["/t"]
    finally:
        pub.close()


def test_guard_exception_becomes_url_refused(guard, internal):
    with pytest.raises(UrlRefusedError) as ei:
        ug.check_url(f"{internal.base}/x")
    assert "internal address" in str(ei.value)


def test_url_guard_scope_restores_previous(internal):
    assert ug.get_url_guard() is None
    with ug.using_url_guard(lambda u: None):
        assert ug.get_url_guard() is not None
    assert ug.get_url_guard() is None


# ── urllib paths ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_urlopen_redirect_hop_refused(guard, internal, code):
    pub = _public({"/r": _redirect(f"{internal.base}/t", code)})
    try:
        with pytest.raises(UrlRefusedError):
            ug.urlopen(f"{pub.base}/r", timeout=5)
        assert internal.hits == []
    finally:
        pub.close()


def test_pdf_remote_download_redirect_refused(guard, internal, tmp_path):
    from arcus.provider_runtime.providers.pdf.pdf import PdfProvider

    pub = _public({"/doc.pdf": _redirect(f"{internal.base}/secret.pdf")})
    try:
        p = PdfProvider()
        d = p.matches(f"{pub.base}/doc.pdf")
        with pytest.raises(UrlRefusedError):
            p.extract(d, _ctx(tmp_path))
        assert internal.hits == []
    finally:
        pub.close()


def test_docs_remote_download_redirect_refused(guard, internal, tmp_path):
    from arcus.provider_runtime.providers.docs.docs import DocsProvider

    pub = _public({"/a.docx": _redirect(f"{internal.base}/secret.docx")})
    try:
        p = DocsProvider()
        d = p.matches(f"{pub.base}/a.docx")
        with pytest.raises(UrlRefusedError):
            p.extract(d, _ctx(tmp_path))
        assert internal.hits == []
    finally:
        pub.close()


def test_image_remote_download_redirect_refused(guard, internal, tmp_path):
    from arcus.provider_runtime.providers.image.image import ImageProvider

    pub = _public({"/a.png": _redirect(f"{internal.base}/secret.png")})
    try:
        p = ImageProvider()
        d = p.matches(f"{pub.base}/a.png")
        with pytest.raises(UrlRefusedError):
            p.extract(d, _ctx(tmp_path))
        assert internal.hits == []
    finally:
        pub.close()


def test_slide_deck_fetch_html_redirect_refused(guard, internal):
    from arcus.provider_runtime.providers.html import slide_deck

    pub = _public({"/deck": _redirect(f"{internal.base}/t")})
    try:
        with pytest.raises(UrlRefusedError):
            slide_deck.fetch_html(f"{pub.base}/deck")
        assert internal.hits == []
    finally:
        pub.close()


def test_file_extract_detect_type_redirect_refused(guard, internal):
    from arcus.provider_runtime.providers._shared import file_extract

    pub = _public({"/f": _redirect(f"{internal.base}/t")})
    try:
        with pytest.raises(UrlRefusedError):
            file_extract.detect_type(f"{pub.base}/f")
        assert internal.hits == []
    finally:
        pub.close()


def test_tco_resolution_redirect_refused_leaves_link(guard, internal, monkeypatch):
    from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

    pub = _public({"/abc": _redirect(f"{internal.base}/t")})
    try:
        monkeypatch.setattr(fp, "_TCO_PATTERN", r"http://127\.0\.0\.1:\d+/abc")
        text = f"see {pub.base}/abc now"
        assert fp._resolve_tco_links(text) == text
        assert internal.hits == []
    finally:
        pub.close()


def test_factory_reports_refusal(guard, internal, tmp_path):
    from arcus.provider_runtime import Factory, register_defaults
    from arcus.provider_runtime.registry import ProviderRegistry
    from arcus.provider_runtime.types import EXIT_CODES

    pub = _public({"/doc.pdf": _redirect(f"{internal.base}/secret.pdf")})
    try:
        reg = ProviderRegistry()
        register_defaults(reg)
        code = Factory(registry=reg).run(f"{pub.base}/doc.pdf", out_dir=tmp_path)
        assert code == EXIT_CODES["URL_REFUSED"]
        assert internal.hits == []
        events = [json.loads(line) for line in
                  (tmp_path / ".log" / "extract-log.ndjson").read_text().splitlines()]
        failed = [e for e in events if e.get("event") == "failed"]
        assert failed and "refused" in json.dumps(failed[-1])
    finally:
        pub.close()


# ── yt-dlp ──────────────────────────────────────────────────────────────────

def test_ytdlp_request_refused(guard, internal):
    from arcus.provider_runtime.providers.youtube import ytdlp_adapter

    with pytest.raises(UrlRefusedError):
        ytdlp_adapter.fetch_metadata(f"{internal.base}/watch")
    assert internal.hits == []


def test_ytdlp_final_url_after_redirect_refused(guard, internal):
    """yt-dlp follows redirects internally; the final URL is still refused."""
    from arcus.provider_runtime.providers.youtube import ytdlp_adapter

    pub = _public({"/v": _redirect(f"{internal.base}/t")})
    try:
        with pytest.raises(UrlRefusedError):
            ytdlp_adapter.fetch_metadata(f"{pub.base}/v")
    finally:
        pub.close()


def test_ytdlp_captions_request_refused(guard, internal, tmp_path):
    from arcus.provider_runtime.providers.youtube import ytdlp_adapter
    from arcus.provider_runtime.providers.youtube.ytdlp_adapter import SubtitleTrack

    with pytest.raises(UrlRefusedError):
        ytdlp_adapter.fetch_captions(
            url=f"{internal.base}/watch", video_id="aaaaaaaaaaa", preferred_lang=None,
            available_tracks=[SubtitleTrack(lang="en", source="uploader")], work_dir=tmp_path,
        )
    assert internal.hits == []


# ── headless browser ────────────────────────────────────────────────────────

def _browser_launch_kwargs():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as p:
        if os.path.exists(p.chromium.executable_path):
            return {}
    if os.path.exists("/Applications/Google Chrome.app"):
        return {"channel": "chrome"}
    return None


_LAUNCH = _browser_launch_kwargs()
browser = pytest.mark.skipif(_LAUNCH is None, reason="no Chromium available for Playwright")


@pytest.fixture
def fetch_page_mod(monkeypatch):
    from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

    monkeypatch.setattr(fp, "_launch_browser",
                        lambda p: p.chromium.launch(headless=True, **(_LAUNCH or {})))
    # Shorten the settle waits so the browser tests stay quick.
    return fp


def _int(internal, path="/t"):
    return f"{internal.base}{path}"


@browser
@pytest.mark.parametrize("kind", ["http302", "meta", "js"])
def test_browser_navigation_redirect_refused(guard, internal, fetch_page_mod, kind):
    target = _int(internal)
    routes = {
        "http302": {"/p": _redirect(target)},
        "meta": {"/p": _html(f'<meta http-equiv="refresh" content="0;url={target}">{LONG}')},
        "js": {"/p": _html(f'<script>location.href="{target}"</script>{LONG}')},
    }[kind]
    pub = _public(routes)
    try:
        with pytest.raises(UrlRefusedError):
            fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert internal.hits == []
    finally:
        pub.close()


@browser
def test_browser_subresources_refused_page_still_extracts(guard, internal, fetch_page_mod):
    body = (
        f'<img src="{_int(internal, "/img")}">'
        f'<iframe src="{_int(internal, "/frame")}"></iframe>'
        f'<script>try{{new WebSocket("ws://127.0.0.1:{internal.port}/ws")}}catch(e){{}}'
        f'fetch("{_int(internal, "/xhr")}").catch(()=>{{}})</script>'
        f"<article><h1>Title</h1>{LONG}</article>"
    )
    pub = _public({"/p": _html(body)})
    try:
        out = fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert out and "Readable article sentence" in out
        assert internal.hits == []
    finally:
        pub.close()


@browser
def test_browser_popup_refused(guard, internal, fetch_page_mod):
    body = f'<script>window.open("{_int(internal, "/pop")}")</script><article>{LONG}</article>'
    pub = _public({"/p": _html(body)})
    try:
        fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert internal.hits == []
    finally:
        pub.close()


@browser
def test_html2md_static_fetch_redirect_refused(guard, internal, fetch_page_mod):
    """The html2md stage re-fetches the page outside the browser; a server that
    redirects only that client must not reach the refused address."""

    def cloak(h):
        if "Chrome" in (h.headers.get("User-Agent") or ""):
            return _html(f"<article><h1>T</h1>{LONG}</article>")
        return _redirect(_int(internal))

    pub = _public({"/p": cloak})
    try:
        try:
            fetch_page_mod.fetch_page(f"{pub.base}/p")
        except UrlRefusedError:
            pass
        assert internal.hits == []
    finally:
        pub.close()


@browser
def test_x_tweet_redirect_refused(guard, internal, fetch_page_mod):
    pub = _public({"/status/1": _redirect(_int(internal))})
    try:
        with pytest.raises(UrlRefusedError):
            fetch_page_mod.fetch_x_tweet(f"{pub.base}/status/1")
        assert internal.hits == []
    finally:
        pub.close()


@browser
def test_x_thread_redirect_refused(guard, internal, fetch_page_mod, monkeypatch):
    monkeypatch.setattr(fetch_page_mod, "_is_x_url", lambda u: True)
    pub = _public({"/status/1": _redirect(_int(internal))})
    try:
        with pytest.raises(UrlRefusedError):
            fetch_page_mod.fetch_page(f"{pub.base}/status/1")
        assert internal.hits == []
    finally:
        pub.close()


@browser
def test_html_provider_surfaces_refusal(guard, internal, fetch_page_mod, tmp_path):
    from arcus.provider_runtime.providers.html.html import HtmlProvider

    pub = _public({"/p": _redirect(_int(internal))})
    try:
        p = HtmlProvider()
        with pytest.raises(UrlRefusedError):
            p.extract(p.matches(f"{pub.base}/p"), _ctx(tmp_path))
        assert internal.hits == []
    finally:
        pub.close()
