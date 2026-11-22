"""Proxy environment variables must never change routing while a guard is set.

Every guarded path is run with each proxy variable set (and with macOS
system proxy exceptions simulated). The refused address AND any rogue proxy
named by the environment must see zero TCP connections.
"""

from __future__ import annotations

import http.server
import os
import socket
import sys
import threading
import time
import urllib.request

import pytest

from arcus.provider_runtime import url_guard as ug

LONG = "<p>" + ("Readable article sentence number one. " * 30) + "</p>"


class _Counter:
    """TCP listener that counts accepts and answers a tiny HTTP 200."""

    def __init__(self):
        self.accepts = 0
        self.s = socket.socket()
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(("127.0.0.1", 0))
        self.s.listen(50)
        self.port = self.s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            self.accepts += 1
            c.settimeout(1)
            try:
                c.recv(4096)
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 8\r\n\r\n<p>x</p>")
            except OSError:
                pass
            c.close()

    def close(self):
        self.s.close()


def _public(routes):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            code, headers, body = routes.get(self.path.split("?")[0], (404, {}, b""))
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}"


ENV_CASES = ["NO_PROXY=*", "no_proxy=*", "HTTP_PROXY=ROGUE", "http_proxy=ROGUE",
             "HTTPS_PROXY=ROGUE", "https_proxy=ROGUE", "ALL_PROXY=ROGUE", "all_proxy=ROGUE",
             "macos_system_bypass"]


@pytest.fixture
def world(request, monkeypatch):
    internal, rogue = _Counter(), _Counter()
    srv, base = _public({
        "/r": (302, {"Location": f"{internal.url}/t"}, b""),
        "/h1": (302, {"Location": "/h2"}, b""),
        "/h2": (302, {"Location": f"{internal.url}/t2"}, b""),
    })
    for k in ("NO_PROXY", "no_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY",
              "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    case = request.param
    if case == "macos_system_bypass":
        # Simulate macOS System Settings proxy exceptions (the default list
        # includes 169.254/16 and *.local). CPython's darwin proxy_bypass()
        # consults them whenever no proxy env var is set.
        if sys.platform != "darwin":
            pytest.skip("macOS system proxy settings only")
        monkeypatch.setattr(urllib.request, "proxy_bypass_macosx_sysconf", lambda host: True)
    else:
        k, v = case.split("=", 1)
        monkeypatch.setenv(k, rogue.url if v == "ROGUE" else v)

    def guard(url):
        if f":{internal.port}" in url:
            raise PermissionError("internal")

    with ug.using_url_guard(guard):
        yield base, internal, rogue
    srv.shutdown()
    internal.close()
    rogue.close()


def _assert_untouched(internal, rogue):
    assert internal.accepts == 0, "refused address was reached"
    assert rogue.accepts == 0, "environment proxy was used"


world_all = pytest.mark.parametrize("world", ENV_CASES, indirect=True)


@world_all
def test_urllib_path(world):
    base, internal, rogue = world
    with pytest.raises(ug.UrlRefusedError):
        ug.urlopen(f"{base}/r", timeout=5)
    _assert_untouched(internal, rogue)


@world_all
@pytest.mark.parametrize("legacy", [False, True], ids=["requests-handler", "urllib-handler"])
def test_ytdlp_path(world, legacy, monkeypatch):
    from arcus.provider_runtime.providers.youtube import ytdlp_adapter

    if legacy:  # yt-dlp's urllib handler (used when `requests` is unavailable)
        real = ytdlp_adapter.YoutubeDL
        monkeypatch.setattr(ytdlp_adapter, "YoutubeDL", lambda o: real(
            {**o, "compat_opts": {"prefer-legacy-http-handler"}}))
    base, internal, rogue = world
    for path in ("/r", "/h1"):
        with pytest.raises(ug.UrlRefusedError):
            ytdlp_adapter.fetch_metadata(f"{base}{path}")
    _assert_untouched(internal, rogue)


def _launch_kwargs():
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


_LAUNCH = _launch_kwargs()


@world_all
@pytest.mark.skipif(_LAUNCH is None, reason="no Chromium available for Playwright")
def test_browser_path(world, monkeypatch):
    from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

    monkeypatch.setattr(fp, "_launch_browser",
                        lambda p, **kw: p.chromium.launch(headless=True, **{**kw, **_LAUNCH}))
    base, internal, rogue = world
    with pytest.raises(ug.UrlRefusedError):
        fp.fetch_page(f"{base}/h1")
    _assert_untouched(internal, rogue)


PROXY_VARS = ("NO_PROXY", "no_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY",
              "https_proxy", "ALL_PROXY", "all_proxy")


def test_guarded_ytdlp_hides_proxy_env_and_restores_it(monkeypatch):
    """Inside a guarded yt-dlp call no proxy variable can steer routing, even
    with overlapping calls in two threads; afterwards the environment is
    exactly as it was."""
    from arcus.provider_runtime.providers.youtube import ytdlp_adapter

    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy.example:3128")
    monkeypatch.setenv("all_proxy", "socks5://corp-proxy.example:1080")
    before = dict(os.environ)
    seen = []

    class Fake:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def urlopen(self, req):
            raise AssertionError("no network")

        def extract_info(self, url, download=False):
            time.sleep(0.3)
            seen.append({k: os.environ.get(k) for k in PROXY_VARS})
            time.sleep(0.3)
            return {"title": "t"}

    monkeypatch.setattr(ytdlp_adapter, "YoutubeDL", Fake)

    def run(delay):
        time.sleep(delay)
        with ug.using_url_guard(lambda u: None):
            ytdlp_adapter.fetch_metadata("https://www.youtube.com/watch?v=aaaaaaaaaaa")

    ts = [threading.Thread(target=run, args=(d,)) for d in (0, 0.15)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert len(seen) == 2
    for env in seen:
        assert not env["HTTP_PROXY"] and not env["HTTPS_PROXY"] and not env["ALL_PROXY"]
        assert not env["http_proxy"] and not env["https_proxy"] and not env["all_proxy"]
        # a NO_PROXY that matches nothing, so neither CPython (which would
        # otherwise fall back to macOS system exceptions) nor libcurl bypasses
        for k in ("NO_PROXY", "no_proxy"):
            assert env[k] and env[k] != "*" and "127.0.0.1" not in env[k]
    assert dict(os.environ) == before


def test_guarded_browser_launch_gets_clean_env(monkeypatch):
    from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("HTTP_PROXY", "http://corp-proxy.example:3128")
    with ug.GuardProxy(lambda u: None) as proxy:
        kw = ug.guarded_launch_kwargs(proxy)
    env = kw["env"]
    assert all(not env.get(k) or k in ("NO_PROXY", "no_proxy") for k in PROXY_VARS)
    assert env.get("NO_PROXY") != "*"
    assert "PATH" in env  # the rest of the environment is passed through
    assert fp  # module imports


def test_guarded_call_restores_environment(monkeypatch):
    """Whatever arcus does to the environment during a guarded call is undone."""
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy.example:3128")
    before = dict(os.environ)
    srv, base = _public({"/ok": (200, {"Content-Type": "text/html"}, b"ok")})
    try:
        with ug.using_url_guard(lambda u: None):
            ug.urlopen(f"{base}/ok", timeout=5).read()
        assert dict(os.environ) == before
    finally:
        srv.shutdown()
