"""Security regressions for the caller URL guard (browser path).

Permanent ports of the review probes that bypassed earlier versions of the
guard. Every case counts *connection-level* reach on the refused address —
TCP accepts and UDP datagrams — and requires both to be zero. Run every
review round (scripts/round-check.sh).
"""

from __future__ import annotations

import http.server
import os
import socket
import threading
import time

import pytest

from arcus.provider_runtime import url_guard as ug

LONG = "<p>" + ("Readable article sentence number one. " * 30) + "</p>"
SECRET = "SECRET-METADATA-CREDENTIAL"


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
pytestmark = pytest.mark.skipif(_LAUNCH is None, reason="no Chromium available for Playwright")


class RawListener:
    """The refused address: counts every TCP accept and UDP datagram on one
    port, answers TCP with a page holding SECRET (so a leak is visible)."""

    def __init__(self):
        self.tcp: list[bytes] = []
        self.udp = 0
        self.s = socket.socket()
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(("127.0.0.1", 0))
        self.s.listen(50)
        self.port = self.s.getsockname()[1]
        self.u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.u.bind(("127.0.0.1", self.port))
        threading.Thread(target=self._tcp_loop, daemon=True).start()
        # Same port on IPv6 loopback, so [::1] targets are counted too.
        self.s6 = None
        try:
            self.s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            self.s6.bind(("::1", self.port))
            self.s6.listen(50)
            threading.Thread(target=self._tcp_loop, args=(self.s6,), daemon=True).start()
        except OSError:
            self.s6 = None
        threading.Thread(target=self._udp_loop, daemon=True).start()

    def _tcp_loop(self, listener=None):
        listener = listener or self.s
        body = f"<article>{SECRET * 20}</article>".encode()
        while True:
            try:
                c, _ = listener.accept()
            except OSError:
                return
            c.settimeout(1)
            try:
                data = c.recv(200)
            except Exception:
                data = b""
            self.tcp.append(data[:80])
            try:
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                          b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                          + str(len(body)).encode() + b"\r\n\r\n" + body)
            except Exception:
                pass
            c.close()

    def _udp_loop(self):
        while True:
            try:
                self.u.recvfrom(65535)
            except OSError:
                return
            self.udp += 1

    def close(self):
        self.s.close()
        self.u.close()
        if self.s6 is not None:
            self.s6.close()


def _public(routes):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            r = routes.get(self.path.split("?")[0])
            if callable(r):
                r = r(self)
            if r is None:
                self.send_response(404)
                self.end_headers()
                return
            code, headers, body = r
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}"


H = {"Content-Type": "text/html"}


def P(b: str):
    return (200, H, b.encode())


def _cloaked(ip):
    def p(h):
        if "KBCapture" in (h.headers.get("User-Agent") or ""):
            return (200, H, b"<p>hi</p>")
        return (302, {"Location": "/p2"}, b"")
    return {"/p": p, "/p2": (302, {"Location": f"http://127.0.0.1:{ip}/latest/meta-data"}, b"")}


CASES = {
    # exfil.py: static fetch short -> page.goto fallback -> 2-hop chain -> internal body
    "exfil_cloaked_two_hop": _cloaked,
    # twohop.py / probe_guard two_hop_302
    "two_hop_302": lambda ip: {"/p": (302, {"Location": "/p2"}, b""),
                               "/p2": (302, {"Location": f"http://127.0.0.1:{ip}/t"}, b"")},
    "dedicated_worker_fetch": lambda ip: {
        "/p": P(f"<article>{LONG}</article><script>new Worker('/w.js')</script>"),
        "/w.js": (200, {"Content-Type": "application/javascript"},
                  f"fetch('http://127.0.0.1:{ip}/w').catch(()=>{{}})".encode())},
    "csp_report_uri": lambda ip: {
        "/p": (200, {"Content-Type": "text/html",
                     "Content-Security-Policy": f"img-src 'none'; report-uri http://127.0.0.1:{ip}/csp"},
               f"<article>{LONG}</article><img src='/x.png'>".encode())},
    "reporting_endpoints": lambda ip: {
        "/p": (200, {"Content-Type": "text/html",
                     "Reporting-Endpoints": f'e="http://127.0.0.1:{ip}/rep"',
                     "Content-Security-Policy": "img-src 'none'; report-to e"},
               f"<article>{LONG}</article><img src='/x.png'>".encode())},
    "preconnect": lambda ip: {
        "/p": P(f"<link rel=preconnect href='http://127.0.0.1:{ip}'>"
                f"<link rel=dns-prefetch href='http://127.0.0.1:{ip}'><article>{LONG}</article>")},
    "webrtc_turn_tcp": lambda ip: {
        "/p": P(f"<article>{LONG}</article><script>try{{const pc=new RTCPeerConnection("
                f"{{iceServers:[{{urls:'turn:127.0.0.1:{ip}?transport=tcp',username:'u',"
                f"credential:'c'}}]}});pc.createDataChannel('x');pc.createOffer()"
                f".then(o=>pc.setLocalDescription(o));}}catch(e){{}}</script>")},
    "webrtc_stun_udp": lambda ip: {
        "/p": P(f"<article>{LONG}</article><script>try{{const pc=new RTCPeerConnection("
                f"{{iceServers:[{{urls:'stun:127.0.0.1:{ip}'}}]}});pc.createDataChannel('x');"
                f"pc.createOffer().then(o=>pc.setLocalDescription(o));}}catch(e){{}}</script>")},
    # round-2 review probes
    "webrtc_turn_tcp_iframe": lambda ip: {
        "/p": P(f"<article>{LONG}</article><script>try{{const f=document.createElement('iframe');"
                f"document.body.appendChild(f);const R=f.contentWindow.RTCPeerConnection;"
                f"const pc=new R({{iceServers:[{{urls:'turn:127.0.0.1:{ip}?transport=tcp',"
                f"username:'u',credential:'c'}}]}});pc.createDataChannel('x');pc.createOffer()"
                f".then(o=>pc.setLocalDescription(o));}}catch(e){{}}</script>")},
    "webrtc_stun_udp_iframe": lambda ip: {
        "/p": P(f"<article>{LONG}</article><script>try{{const f=document.createElement('iframe');"
                f"document.body.appendChild(f);const R=f.contentWindow.RTCPeerConnection;"
                f"const pc=new R({{iceServers:[{{urls:'stun:127.0.0.1:{ip}'}}]}});"
                f"pc.createDataChannel('x');pc.createOffer().then(o=>pc.setLocalDescription(o));"
                f"}}catch(e){{}}</script>")},
    "two_hop_to_ipv6_loopback": lambda ip: {"/p": (302, {"Location": "/p2"}, b""),
                                            "/p2": (302, {"Location": f"http://[::1]:{ip}/t"}, b"")},
    "sendbeacon": lambda ip: {
        "/p": P(f"<article>{LONG}</article><script>navigator.sendBeacon("
                f"'http://127.0.0.1:{ip}/b','x')</script>")},
}


@pytest.fixture
def fp(monkeypatch):
    from arcus.provider_runtime.providers.html import _athena_fetch_page as mod

    # Same launch arcus does (headless + whatever guard-proxy settings it
    # passes), on whichever Chromium build this machine has.
    monkeypatch.setattr(mod, "_launch_browser",
                        lambda p, **kw: p.chromium.launch(headless=True, **{**kw, **_LAUNCH}))
    return mod


@pytest.mark.parametrize("name", list(CASES))
def test_probe_never_reaches_refused_address(fp, name):
    internal = RawListener()
    srv, base = _public(CASES[name](internal.port))

    def guard(url):
        if f":{internal.port}" in url:
            raise PermissionError("internal")

    out = None
    try:
        with ug.using_url_guard(guard):
            try:
                out = fp.fetch_page(f"{base}/p")
            except ug.UrlRefusedError:
                pass
        time.sleep(1.5)  # late beacons / reports / ICE gathering
        assert internal.tcp == [] and internal.udp == 0, (internal.tcp[:2], internal.udp)
        assert SECRET not in (out or "")
    finally:
        srv.shutdown()
        internal.close()



def test_webrtc_absent_in_fresh_iframes():
    """iframe_rtc.py: RTCPeerConnection is gone in the top frame AND in a
    freshly created same-origin iframe."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p, ug.GuardProxy(lambda u: None) as px:
        b = p.chromium.launch(headless=True, **_LAUNCH, **ug.guarded_launch_kwargs(px))
        try:
            ctx = b.new_context(service_workers="block")
            ug.install_browser_guard(ctx, lambda u: None, px)
            pg = ctx.new_page()
            pg.set_content("<p>x</p>")
            assert pg.evaluate("typeof RTCPeerConnection") == "undefined"
            assert pg.evaluate(
                "(()=>{const f=document.createElement('iframe');document.body.appendChild(f);"
                "return typeof f.contentWindow.RTCPeerConnection})()") == "undefined"
        finally:
            b.close()
    assert not ug._LIVE_PROXIES
