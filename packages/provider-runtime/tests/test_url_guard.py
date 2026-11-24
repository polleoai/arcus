"""Caller URL guard: every fetch path re-checks redirects and sub-requests.

Each test runs two local servers. PUBLIC serves pages that redirect / embed /
navigate to INTERNAL; the guard refuses INTERNAL's port. The core assertion
is always the same: INTERNAL records zero hits.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from arcus.provider_runtime import url_guard as ug
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.url_guard import UrlRefusedError

LONG = "<p>" + ("Readable article sentence number one. " * 30) + "</p>"


class _Server:
    """Records every TCP accept (`accepts`) and every parsed HTTP path (`hits`).

    `accepts` catches non-HTTP traffic too (WebRTC TURN/STUN, raw TCP)."""

    def __init__(self, routes=None):
        self.hits: list[str] = []
        self.accepts = 0
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

        class S(http.server.ThreadingHTTPServer):
            def verify_request(self, request, client_address):
                outer.accepts += 1
                return True

        self.httpd = S(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        # UDP on the same port: WebRTC STUN/TURN-over-UDP and QUIC are invisible
        # to the TCP accept counter.
        self.datagrams = 0
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._udp.bind(("127.0.0.1", self.port))
            threading.Thread(target=self._udp_loop, daemon=True).start()
        except OSError:
            self._udp.close()
            self._udp = None

    def _udp_loop(self):
        while True:
            try:
                self._udp.recvfrom(65535)
            except OSError:
                return
            self.datagrams += 1

    @property
    def touched(self) -> int:
        return self.accepts + self.datagrams

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._udp is not None:
            self._udp.close()


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
        assert internal.accepts == 0, internal.hits
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
        assert internal.accepts == 0, internal.hits
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
        assert internal.accepts == 0, internal.hits
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
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


def test_slide_deck_fetch_html_redirect_refused(guard, internal):
    from arcus.provider_runtime.providers.html import slide_deck

    pub = _public({"/deck": _redirect(f"{internal.base}/t")})
    try:
        with pytest.raises(UrlRefusedError):
            slide_deck.fetch_html(f"{pub.base}/deck")
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


def test_file_extract_detect_type_redirect_refused(guard, internal):
    from arcus.provider_runtime.providers._shared import file_extract

    pub = _public({"/f": _redirect(f"{internal.base}/t")})
    try:
        with pytest.raises(UrlRefusedError):
            file_extract.detect_type(f"{pub.base}/f")
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


def test_tco_resolution_redirect_refused_leaves_link(guard, internal, monkeypatch):
    from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

    pub = _public({"/abc": _redirect(f"{internal.base}/t")})
    try:
        monkeypatch.setattr(fp, "_TCO_PATTERN", r"http://127\.0\.0\.1:\d+/abc")
        text = f"see {pub.base}/abc now"
        assert fp._resolve_tco_links(text) == text
        assert internal.accepts == 0, internal.hits
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
        assert internal.accepts == 0, internal.hits
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
    assert internal.accepts == 0, internal.hits


def test_ytdlp_final_url_after_redirect_refused(guard, internal):
    """yt-dlp follows redirects internally; every hop is still checked first."""
    from arcus.provider_runtime.providers.youtube import ytdlp_adapter

    pub = _public({"/v": _redirect(f"{internal.base}/t")})
    try:
        with pytest.raises(UrlRefusedError):
            ytdlp_adapter.fetch_metadata(f"{pub.base}/v")
        assert internal.accepts == 0, internal.hits  # hop refused BEFORE it is requested
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
    assert internal.accepts == 0, internal.hits


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

    def _launch(p, **kw):
        # Same launch arcus does (headless + whatever guard-proxy settings it
        # passes), on whichever Chromium build this machine has.
        return p.chromium.launch(headless=True, **{**kw, **(_LAUNCH or {})})

    monkeypatch.setattr(fp, "_launch_browser", _launch)
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
        assert internal.accepts == 0, internal.hits
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
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


@browser
def test_browser_popup_refused(guard, internal, fetch_page_mod):
    body = f'<script>window.open("{_int(internal, "/pop")}")</script><article>{LONG}</article>'
    pub = _public({"/p": _html(body)})
    try:
        fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


@browser
def test_html2md_never_refetches_the_page(guard, internal, fetch_page_mod):
    """#13: html2md converts the browser-rendered DOM. Nothing but the browser
    contacts the server, so a server that cloaks other clients (here: redirects
    them to a refused address) never gets a second, unguarded-looking request."""
    seen = []

    def cloak(h):
        ua = h.headers.get("User-Agent") or ""
        seen.append(ua)
        if "Chrome" in ua:
            return _html(f"<article><h1>T</h1>{LONG}</article>")
        return _redirect(_int(internal))

    pub = _public({"/p": cloak})
    try:
        out = fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert out and "Readable article sentence" in out
        assert seen and all("Chrome" in ua for ua in seen), seen
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


@browser
def test_html2md_keeps_javascript_rendered_content(fetch_page_mod):
    """#13 (no guard set): the markdown comes from the DOM after JavaScript ran."""
    body = (
        f"<article><h1>T</h1><p id=s>Static placeholder text.</p>{LONG}</article>"
        "<script>document.getElementById('s').textContent="
        "'Hydrated by JavaScript after load.'</script>"
    )
    pub = _public({"/p": _html(body)})
    try:
        out = fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert out and "Hydrated by JavaScript after load." in out
        assert "Static placeholder text." not in out
    finally:
        pub.close()


@browser
def test_html2md_keeps_shadow_dom_content(fetch_page_mod):
    """#13: content a web component moves into its shadow root after load
    (MDN's code examples do this) is part of the rendered page."""
    body = (
        f"<article><h1>T</h1>{LONG}<code-box></code-box></article>"
        "<script>customElements.define('code-box', class extends HTMLElement {"
        "connectedCallback(){ this.attachShadow({mode:'open'}).innerHTML ="
        "'<p>Text that lives only in the shadow root.</p>'; } });</script>"
    )
    pub = _public({"/p": _html(body)})
    try:
        out = fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert out and "Text that lives only in the shadow root." in out
    finally:
        pub.close()


@browser
def test_x_tweet_redirect_refused(guard, internal, fetch_page_mod):
    pub = _public({"/status/1": _redirect(_int(internal))})
    try:
        with pytest.raises(UrlRefusedError):
            fetch_page_mod.fetch_x_tweet(f"{pub.base}/status/1")
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


@browser
def test_x_thread_redirect_refused(guard, internal, fetch_page_mod, monkeypatch):
    monkeypatch.setattr(fetch_page_mod, "_is_x_url", lambda u: True)
    pub = _public({"/status/1": _redirect(_int(internal))})
    try:
        with pytest.raises(UrlRefusedError):
            fetch_page_mod.fetch_page(f"{pub.base}/status/1")
        assert internal.accepts == 0, internal.hits
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
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


# ── round 2: multi-hop chains, WebRTC, workers, concurrency, guard contract ──

def test_guard_returning_value_is_a_refusal(internal):
    """A predicate-style guard (returns True/False) must fail closed."""
    with ug.using_url_guard(lambda u: f":{internal.port}" in u):
        with pytest.raises(UrlRefusedError):
            ug.urlopen(f"{internal.base}/t", timeout=5)
        with pytest.raises(UrlRefusedError):
            ug.check_url("http://example.com/")  # returns False -> still refused
    assert internal.accepts == 0


def test_scoped_guard_is_per_thread(guard, internal):
    """Another thread entering/leaving its own scope never disables ours."""
    import time

    def slow(h):
        time.sleep(1.0)
        return _redirect(_int(internal))

    pub = _public({"/slow": slow})
    errors = []

    def worker():
        try:
            ug.urlopen(f"{pub.base}/slow", timeout=10)
        except UrlRefusedError as e:
            errors.append(e)

    try:
        t = threading.Thread(target=worker)
        # the worker thread inherits nothing implicitly; give it our scope
        import contextvars
        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(worker))
        t.start()
        time.sleep(0.2)
        with ug.using_url_guard(None):  # unrelated scope in THIS thread
            time.sleep(0.2)
        t.join(10)
        assert errors, "redirect hop was not refused"
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


def test_guard_snapshot_survives_global_change(internal):
    """Removing the process guard mid-fetch does not un-guard that fetch."""
    import time

    def slow(h):
        time.sleep(1.0)
        return _redirect(_int(internal))

    pub = _public({"/slow": slow})
    ug.set_url_guard(lambda u: (_ for _ in ()).throw(PermissionError("x"))
                     if f":{internal.port}" in u else None)
    errors = []

    def worker():
        try:
            ug.urlopen(f"{pub.base}/slow", timeout=10)
        except UrlRefusedError as e:
            errors.append(e)

    try:
        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.3)
        ug.set_url_guard(None)
        t.join(10)
        assert errors and internal.accepts == 0, internal.hits
    finally:
        ug.set_url_guard(None)
        pub.close()


def test_browser_guard_requires_websocket_routing(guard):
    class OldContext:
        def route(self, *a, **k):
            pass

    with pytest.raises(RuntimeError):
        ug.install_browser_guard(OldContext())


@browser
@pytest.mark.parametrize("hops", [2, 3])
def test_browser_multi_hop_redirect_refused(guard, internal, fetch_page_mod, hops):
    routes = {}
    for i in range(1, hops):
        routes[f"/h{i}"] = _redirect(f"/h{i + 1}")
    routes[f"/h{hops}"] = _redirect(_int(internal, "/latest"))
    pub = _public(routes)
    try:
        with pytest.raises(UrlRefusedError):
            fetch_page_mod.fetch_page(f"{pub.base}/h1")
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


@browser
def test_browser_cloaked_fallback_multi_hop_refused(guard, internal, fetch_page_mod):
    """Static fetch sees a short page, so fetch_page falls back to page.goto;
    the browser is then sent through a 2-hop chain to the refused address."""

    def p1(h):
        if "KBCapture" in (h.headers.get("User-Agent") or ""):
            return _html("<p>hi</p>")
        return _redirect("/p2")

    pub = _public({"/p": p1, "/p2": _redirect(_int(internal, "/latest"))})
    try:
        with pytest.raises(UrlRefusedError):
            fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


@browser
def test_browser_redirecting_subresource_refused(guard, internal, fetch_page_mod):
    body = (f'<img src="/img1"><iframe src="/fr1"></iframe>'
            f'<script src="/js1"></script><article><h1>T</h1>{LONG}</article>')
    pub = _public({
        "/p": _html(body),
        "/img1": _redirect("/img2"), "/img2": _redirect(_int(internal, "/img")),
        "/fr1": _redirect(_int(internal, "/fr")),
        "/js1": _redirect("/js2"), "/js2": _redirect(_int(internal, "/js")),
    })
    try:
        out = fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert out and "Readable article sentence" in out
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


@browser
def test_browser_webrtc_cannot_reach_refused(guard, internal, fetch_page_mod):
    ip = internal.port
    script = (
        "<script>try{for(const u of ['turn:127.0.0.1:%d?transport=tcp',"
        "'turn:127.0.0.1:%d?transport=udp','stun:127.0.0.1:%d']){"
        "const pc=new RTCPeerConnection({iceServers:[{urls:u,username:'u',credential:'c'}]});"
        "pc.createDataChannel('x');pc.createOffer().then(o=>pc.setLocalDescription(o));}}"
        "catch(e){}</script>" % (ip, ip, ip)
    )
    pub = _public({"/p": _html(f"<article><h1>T</h1>{LONG}</article>{script}")})
    try:
        out = fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert out and "Readable article sentence" in out
        assert internal.touched == 0, (internal.accepts, internal.datagrams, internal.hits)
    finally:
        pub.close()


@browser
def test_browser_dedicated_worker_refused(guard, internal, fetch_page_mod):
    worker_js = (f'fetch("{_int(internal, "/w1")}").catch(()=>0);'
                 f'fetch("/wr").catch(()=>0);'
                 f'try{{new WebSocket("ws://127.0.0.1:{internal.port}/ws")}}catch(e){{}}')
    pub = _public({
        "/p": _html(f'<script>new Worker("/w.js")</script><article><h1>T</h1>{LONG}</article>'),
        "/w.js": (200, {"Content-Type": "application/javascript"}, worker_js.encode()),
        "/wr": _redirect(_int(internal, "/w2")),
    })
    try:
        out = fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert out and "Readable article sentence" in out
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


@browser
def test_x_tweet_multi_hop_refused(guard, internal, fetch_page_mod):
    pub = _public({"/status/1": _redirect("/status/2"),
                   "/status/2": _redirect(_int(internal))})
    try:
        with pytest.raises(UrlRefusedError):
            fetch_page_mod.fetch_x_tweet(f"{pub.base}/status/1")
        assert internal.accepts == 0, internal.hits
    finally:
        pub.close()


# ── guard proxy: pinning, fail-closed, bounds, yt-dlp downloaders ───────────

def _proxied_get(proxy_url: str, url: str):
    import urllib.request
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    return opener.open(url, timeout=10)


def test_proxy_refuses_name_with_any_refused_address(internal, monkeypatch):
    """A name resolving to both an allowed and a refused address is refused
    outright (rule: every resolved address must pass), and nothing is
    contacted."""
    real = socket.getaddrinfo

    def fake(host, *a, **k):
        if host == "mixed.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", internal.port)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.2", internal.port))]
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", fake)

    def policy(url):
        if "//127.0.0.1:" in url:  # the IP-literal check of the internal address
            raise PermissionError("internal")

    with ug.GuardProxy(policy) as proxy:
        with pytest.raises(Exception):
            _proxied_get(proxy.url, f"http://mixed.test:{internal.port}/x")
        assert proxy.refused
    assert internal.accepts == 0, internal.hits


def test_proxy_connects_to_checked_address_only(internal, monkeypatch):
    """The proxy connects to the exact address it checked (DNS pinned): a
    second, different answer at connect time is never used."""
    real = socket.getaddrinfo
    answers = iter([("127.0.0.1", internal.port)])  # first answer only

    def fake(host, *a, **k):
        if host == "pinned.test":
            ip = next(answers)  # a second lookup would raise StopIteration
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ip)]
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with ug.GuardProxy(lambda u: None) as proxy:
        with _proxied_get(proxy.url, f"http://pinned.test:{internal.port}/ok") as r:
            assert r.status == 200
    assert internal.hits == ["/ok"]


def test_proxy_connection_cap_and_idle_timeout(internal):
    with ug.GuardProxy(lambda u: None, max_connections=2, idle_timeout=0.5) as proxy:
        host, port = proxy.address
        socks = [socket.create_connection((host, port)) for _ in range(4)]
        import time
        time.sleep(1.5)
        # idle connections were dropped, and at most 2 were ever served
        closed = 0
        for sk in socks:
            sk.settimeout(1)
            try:
                if sk.recv(1) == b"":
                    closed += 1
            except OSError:
                closed += 1
            sk.close()
        assert closed == 4
        assert proxy.peak_connections <= 2


def test_ytdlp_forces_proxy_and_native_downloader(guard, monkeypatch, tmp_path):
    from arcus.provider_runtime.providers.youtube import ytdlp_adapter
    from arcus.provider_runtime.providers.youtube.ytdlp_adapter import SubtitleTrack

    seen = []

    class Fake:
        def __init__(self, opts):
            seen.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def urlopen(self, req):
            raise AssertionError("no network in this test")

        def extract_info(self, url, download=False):
            return {"title": "t"}

        def download(self, urls):
            (tmp_path / "aaaaaaaaaaa.en.vtt").write_text("WEBVTT\n")

    monkeypatch.setattr(ytdlp_adapter, "YoutubeDL", Fake)
    ytdlp_adapter.fetch_metadata("https://www.youtube.com/watch?v=aaaaaaaaaaa")
    ytdlp_adapter.fetch_captions(
        url="https://www.youtube.com/watch?v=aaaaaaaaaaa", video_id="aaaaaaaaaaa",
        preferred_lang=None, available_tracks=[SubtitleTrack(lang="en", source="uploader")],
        work_dir=tmp_path)
    assert len(seen) == 2
    for opts in seen:
        assert opts["proxy"].startswith("http://127.0.0.1:")
        assert opts["external_downloader"] == {"default": "native"}
        assert opts["hls_prefer_native"] is True
        assert not opts.get("postprocessors")


@browser
def test_browser_fails_closed_when_proxy_dies(guard, internal, fetch_page_mod):
    """Proxy killed mid-fetch: the fetch errors and nothing reaches the
    refused address (Chromium never falls back to a direct connection)."""

    def p(h):
        for proxy in list(ug._LIVE_PROXIES):
            proxy.kill()
        return _html(f'<script>location.href="{_int(internal)}"</script>'
                     f'<img src="{_int(internal, "/img")}"><article>{LONG}</article>')

    pub = _public({"/p": p})
    try:
        with pytest.raises(UrlRefusedError):
            fetch_page_mod.fetch_page(f"{pub.base}/p")
        assert internal.touched == 0, internal.hits
    finally:
        pub.close()


@browser
def test_browser_launch_disables_quic_prefetch_webrtc(guard, monkeypatch):
    from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

    with ug.GuardProxy(lambda u: None) as proxy:
        kw = fp._guarded_launch_kwargs(proxy)
    args = " ".join(kw["args"])
    assert kw["proxy"]["server"] == proxy.url
    for flag in ("--disable-quic", "--dns-prefetch-disable", "<-loopback>",
                 "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"):
        assert flag in args


# ── round 2 LOWs: pipelining, pool workers, deadlines ───────────────────────

class _RawUpstream:
    """Allowed upstream that ignores `Connection: close` and records EVERY
    byte it is sent (a hostile allowed server)."""

    def __init__(self, reply=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok", trickle=False):
        self.received = b""
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        self.s.listen(8)
        self.port = self.s.getsockname()[1]
        self.reply, self.trickle = reply, trickle
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        import time
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            c.settimeout(1.0)
            try:
                if self.trickle:
                    c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
                    for _ in range(100):  # never idle, never done
                        c.sendall(b"x")
                        time.sleep(0.1)
                else:
                    while True:
                        try:
                            data = c.recv(65536)
                        except OSError:
                            break
                        if not data:
                            break
                        self.received += data
                        c.sendall(self.reply)
            except OSError:
                pass
            c.close()

    def close(self):
        self.s.close()


def test_proxy_one_request_per_client_connection():
    """A second (pipelined) request on the same client connection must never
    be delivered to the first, already-approved upstream."""
    up = _RawUpstream()
    other = _Server()
    try:
        with ug.GuardProxy(lambda u: None) as proxy:
            c = socket.create_connection(proxy.address)
            c.sendall(
                f"GET http://127.0.0.1:{up.port}/one HTTP/1.1\r\nHost: 127.0.0.1:{up.port}\r\n\r\n"
                f"GET http://127.0.0.1:{other.port}/two HTTP/1.1\r\nHost: 127.0.0.1:{other.port}\r\n\r\n"
                .encode())
            c.settimeout(3)
            try:
                while c.recv(65536):
                    pass
            except OSError:
                pass
            c.close()
        assert b"/one" in up.received
        assert b"/two" not in up.received, up.received
    finally:
        up.close()
        other.close()


def test_pool_worker_without_scope_warns(monkeypatch):
    """A thread-pool worker doesn't inherit using_url_guard: arcus warns
    (once) instead of fetching silently unguarded."""
    from concurrent.futures import ThreadPoolExecutor

    pub = _public({"/ok": _html("<p>ok</p>")})
    monkeypatch.setattr(ug, "_warned_unscoped_worker", False, raising=False)
    try:
        with ug.using_url_guard(lambda u: None):
            with ThreadPoolExecutor(1) as ex:
                with pytest.warns(RuntimeWarning, match="set_url_guard"):
                    ex.submit(lambda: ug.urlopen(f"{pub.base}/ok", timeout=5).read()).result()
    finally:
        pub.close()


def test_proxy_dns_lookup_is_bounded(monkeypatch):
    import time
    real = socket.getaddrinfo

    def slow(host, *a, **k):
        if host == "slow.test":
            time.sleep(5)
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    with ug.GuardProxy(lambda u: None, connect_timeout=0.5) as proxy:
        t0 = time.monotonic()
        with pytest.raises(Exception):
            _proxied_get(proxy.url, "http://slow.test:9/x")
        assert time.monotonic() - t0 < 3


def test_proxy_connection_total_deadline():
    """A never-idle, never-finishing upstream is cut at the total deadline."""
    import time
    up = _RawUpstream(trickle=True)
    try:
        with ug.GuardProxy(lambda u: None, idle_timeout=5, total_timeout=1.0) as proxy:
            c = socket.create_connection(proxy.address)
            c.sendall(f"GET http://127.0.0.1:{up.port}/x HTTP/1.1\r\nHost: h\r\n\r\n".encode())
            c.settimeout(5)
            t0 = time.monotonic()
            try:
                while c.recv(65536):
                    pass
            except OSError:
                pass
            assert time.monotonic() - t0 < 3
            c.close()
    finally:
        up.close()


@pytest.mark.parametrize("cl", [["-5"], ["abc"], ["10", "20"]])
def test_proxy_refuses_malformed_content_length(cl):
    """Negative, non-numeric or conflicting Content-Length: nothing is sent
    upstream (no mis-framed / smuggled request)."""
    up = _RawUpstream()
    try:
        with ug.GuardProxy(lambda u: None) as proxy:
            c = socket.create_connection(proxy.address)
            hdrs = "".join(f"Content-Length: {v}\r\n" for v in cl)
            c.sendall(f"POST http://127.0.0.1:{up.port}/x HTTP/1.1\r\nHost: h\r\n{hdrs}\r\n"
                      f"0123456789GET /smuggled HTTP/1.1\r\n\r\n".encode())
            c.settimeout(2)
            try:
                c.recv(100)
            except OSError:
                pass
            c.close()
        time.sleep(0.3)
        assert up.received == b"", up.received
    finally:
        up.close()


def test_proxy_request_body_is_streamed_and_deadlined():
    """A huge declared body is streamed (not buffered whole) and a client that
    trickles it is cut at the total deadline."""
    up = _RawUpstream()
    try:
        with ug.GuardProxy(lambda u: None, idle_timeout=5, total_timeout=1.0) as proxy:
            c = socket.create_connection(proxy.address)
            c.sendall(f"POST http://127.0.0.1:{up.port}/x HTTP/1.1\r\nHost: h\r\n"
                      f"Content-Length: 10000000000\r\n\r\n".encode())
            t0 = time.monotonic()
            closed = False
            for _ in range(40):  # trickle for up to ~4s
                try:
                    c.sendall(b"y" * 10)
                except OSError:
                    closed = True
                    break
                time.sleep(0.1)
            if not closed:
                c.settimeout(2)
                try:
                    closed = c.recv(1) == b""
                except OSError:
                    closed = True
            assert closed and time.monotonic() - t0 < 3.5
            c.close()
        assert b"y" in up.received  # forwarded as it arrived, not held back
    finally:
        up.close()
