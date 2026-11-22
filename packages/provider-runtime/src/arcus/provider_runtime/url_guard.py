"""Caller-supplied URL policy for every network fetch arcus makes.

arcus stays policy-free: it does not decide which hosts are allowed. A caller
that hands arcus untrusted URLs installs a guard — a callable
``guard(url: str) -> None`` that raises to refuse — and arcus applies it to
*every* URL and connection it is about to make, not just the first URL:

- urllib fetches (downloads, HEAD probes, t.co resolution, static HTML): the
  input URL, every redirect hop before it is requested, and the final URL;
- the headless browser and yt-dlp: all of their traffic is forced through a
  per-fetch **guard proxy** (127.0.0.1, random port, lives only for that
  fetch). The proxy checks every request / CONNECT target — including every
  redirect hop — resolves the name itself, checks *every* resolved address,
  and connects only to an address it checked (DNS pinned). The browser also
  checks each request's full URL before it is sent, runs with QUIC, DNS
  prefetch and non-proxied WebRTC disabled, and has WebRTC removed from pages.

Guard contract: return ``None`` to allow; raise (any exception) to refuse.
Anything else — e.g. a predicate returning ``True``/``False`` — is treated as
a refusal (fail closed), with a reason saying so.

Scoping: ``set_url_guard()`` sets the process default; ``using_url_guard()``
overrides it for the current thread / async task only (a ContextVar), so
overlapping scopes in other threads never affect each other. Every fetch
snapshots the guard at entry and uses that snapshot for its whole duration.

Why module-level and not a field on ExtractionContext: some fetches happen
outside ``extract()`` (``Provider.predict_slug`` may fetch metadata), and the
vendored fetch helpers are plain functions consumers call directly.

With no guard installed, behaviour is identical to earlier releases.

A refused page (the input URL, a redirect of it, or a navigation of the page
being extracted) raises :class:`UrlRefusedError`; providers never turn it into
a silent empty result. A refused sub-resource (image, frame, script, worker
request, WebSocket, popup) is blocked and the page is still extracted. If the
guard proxy cannot start or stops mid-fetch, the fetch fails — it never runs
unproxied.
"""

from __future__ import annotations

import contextlib
import contextvars
import selectors
import shutil
import socket
import threading
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urljoin, urlsplit

UrlGuard = Callable[[str], None]
UrlOpen = Callable[..., Any]

__all__ = [
    "GuardProxy",
    "UrlGuard",
    "UrlRefusedError",
    "check_url",
    "download",
    "get_url_guard",
    "set_url_guard",
    "urlopen",
    "using_url_guard",
]


class UrlRefusedError(Exception):
    """The caller's URL guard refused a URL arcus was about to fetch.

    Deliberately NOT a subclass of OSError/URLError: the fetch helpers'
    soft-fail ``except OSError`` handlers must never swallow a refusal.
    """

    def __init__(self, url: str, reason: str = "") -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"URL refused by guard: {url}" + (f" ({reason})" if reason else ""))


# ── guard registry ──────────────────────────────────────────────────────────

_lock = threading.Lock()
_default: tuple[UrlGuard | None, UrlOpen | None] = (None, None)
_UNSET = object()
_scoped: contextvars.ContextVar[Any] = contextvars.ContextVar("arcus_url_guard", default=_UNSET)


def set_url_guard(guard: UrlGuard | None, *, urlopen: UrlOpen | None = None) -> None:
    """Install (or, with ``None``, remove) the process-wide default URL guard.

    ``urlopen`` optionally replaces arcus's own urllib opener for the plain
    HTTP paths (e.g. one that pins the address the policy approved). It must
    accept ``(request, timeout=...)`` like ``urllib.request.urlopen``; it then
    owns redirect-hop checks (arcus still checks the initial and final URLs).
    """
    global _default
    with _lock:
        _default = (guard, urlopen if guard is not None else None)


def _current() -> tuple[UrlGuard | None, UrlOpen | None]:
    scoped = _scoped.get()
    return _default if scoped is _UNSET else scoped


def get_url_guard() -> UrlGuard | None:
    """The guard in effect for the current thread / task (scope, else default)."""
    return _current()[0]


@contextlib.contextmanager
def using_url_guard(guard: UrlGuard | None, *, urlopen: UrlOpen | None = None) -> Iterator[None]:
    """Scope a guard to a ``with`` block in the current thread / async task.

    Other threads keep their own scope (or the process default); a thread
    started inside the block does NOT inherit it unless run in a copied
    context (``contextvars.copy_context().run``).
    """
    token = _scoped.set((guard, urlopen if guard is not None else None))
    try:
        yield
    finally:
        _scoped.reset(token)


def check_url(url: str, guard: Any = _UNSET) -> None:
    """Run the guard (the given snapshot, else the current one) on ``url``.

    No-op when no guard is in effect. Any exception the guard raises — or any
    non-None return value — becomes :class:`UrlRefusedError`.
    """
    g = get_url_guard() if guard is _UNSET else guard
    if g is None:
        return
    try:
        result = g(url)
    except UrlRefusedError:
        raise
    except Exception as e:  # the guard's own refusal type is caller-defined
        raise UrlRefusedError(url, str(e) or type(e).__name__) from e
    if result is not None:
        raise UrlRefusedError(
            url, f"guard returned {result!r}; a guard must return None to allow and raise to refuse")


def is_refused(url: str, guard: Any = _UNSET) -> bool:
    try:
        check_url(url, guard)
    except UrlRefusedError:
        return True
    return False


# ── urllib ──────────────────────────────────────────────────────────────────

class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-checks every redirect hop BEFORE urllib requests it."""

    def __init__(self, guard: UrlGuard) -> None:
        super().__init__()
        self._guard = guard

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(urljoin(req.full_url, newurl), self._guard)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(req: urllib.request.Request | str, timeout: float | None = None) -> Any:
    """``urllib.request.urlopen`` with the guard on every hop.

    Raises :class:`UrlRefusedError` on a refused URL; otherwise raises what
    urllib raises.
    """
    guard, override = _current()  # snapshot for the whole fetch
    kwargs: dict[str, Any] = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if guard is None:  # unguarded: exactly the pre-0.7.1 call
        return urllib.request.urlopen(req, **kwargs)
    if isinstance(req, str):
        req = urllib.request.Request(req)
    check_url(req.full_url, guard)
    if override is not None:
        resp = override(req, **kwargs)
    else:
        resp = urllib.request.build_opener(_GuardedRedirectHandler(guard)).open(req, **kwargs)
    try:
        check_url(resp.geturl(), guard)
    except UrlRefusedError:
        resp.close()
        raise
    return resp


def download(url: str, dest: str, timeout: float | None = None) -> None:
    """Guarded replacement for ``urllib.request.urlretrieve(url, dest)``."""
    if get_url_guard() is None:  # unguarded: exactly the pre-0.7.1 call
        urllib.request.urlretrieve(url, dest)
        return
    with urlopen(url, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


# ── guard proxy ─────────────────────────────────────────────────────────────

_LIVE_PROXIES: set[GuardProxy] = set()  # test hook + leak visibility
_HOP_HEADERS = {"proxy-connection", "connection", "keep-alive", "proxy-authorization",
                "proxy-authenticate", "te", "trailer", "upgrade"}


def _host_for_url(host: str) -> str:
    return f"[{host}]" if ":" in host else host


class GuardProxy:
    """Per-fetch HTTP/CONNECT proxy that enforces a guard snapshot.

    For every client request it: checks the target URL (plain HTTP: the full
    absolute URL; CONNECT: ``scheme://host:port/``), resolves the host ONCE,
    checks every resolved address as an IP-literal URL (refusing the request
    if ANY address is refused — a mixed answer is a rebinding signal), then
    connects only to those checked addresses. Plain-HTTP requests are sent
    one per upstream connection (``Connection: close``) so a kept-alive client
    connection can never reuse an approved upstream for a different target.

    Bounds: ``connect_timeout`` per upstream connect, ``idle_timeout`` on any
    silent connection, at most ``max_connections`` concurrent client
    connections (extra ones are closed at once).

    Listens on 127.0.0.1 only, on a random port, for the life of one fetch.
    It is unauthenticated: another local process could use it while it runs,
    but only under the same guard.

    ``kill()`` simulates/flags an abnormal stop; ``failed`` is then True and
    the fetch using it must fail.
    """

    def __init__(self, guard: UrlGuard, *, max_connections: int = 64,
                 connect_timeout: float = 10.0, idle_timeout: float = 30.0) -> None:
        if guard is None:
            raise ValueError("GuardProxy needs a guard")
        self.guard = guard
        self.max_connections = max_connections
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.refused: list[tuple[str, str]] = []
        self.refused_hostports: set[tuple[str, int]] = set()
        self.failed = False
        self.peak_connections = 0
        self._active = 0
        self._socks: set[socket.socket] = set()
        self._mu = threading.Lock()
        self._closed = False
        self._listener: socket.socket | None = None
        self.address: tuple[str, int] = ("127.0.0.1", 0)

    # lifecycle
    def start(self) -> GuardProxy:
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.bind(("127.0.0.1", 0))
        ls.listen(128)
        self._listener = ls
        self.address = ls.getsockname()
        threading.Thread(target=self._accept_loop, daemon=True,
                         name=f"arcus-guard-proxy-{self.address[1]}").start()
        _LIVE_PROXIES.add(self)
        return self

    @property
    def url(self) -> str:
        return f"http://{self.address[0]}:{self.address[1]}"

    def _shutdown(self) -> None:
        with self._mu:
            self._closed = True
            socks = list(self._socks)
            self._socks.clear()
        if self._listener is not None:
            with contextlib.suppress(OSError):
                self._listener.close()
        for s in socks:
            with contextlib.suppress(OSError):
                s.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                s.close()
        _LIVE_PROXIES.discard(self)

    def stop(self) -> None:
        self._shutdown()

    def kill(self) -> None:
        self.failed = True
        self._shutdown()

    def __enter__(self) -> GuardProxy:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # internals
    def _track(self, s: socket.socket) -> bool:
        with self._mu:
            if self._closed:
                return False
            self._socks.add(s)
            return True

    def _untrack(self, s: socket.socket) -> None:
        with self._mu:
            self._socks.discard(s)
        with contextlib.suppress(OSError):
            s.close()

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                if not self._closed:
                    self.failed = True  # listener died under a running fetch
                return
            with self._mu:
                over = self._active >= self.max_connections or self._closed
                if not over:
                    self._active += 1
                    self.peak_connections = max(self.peak_connections, self._active)
            if over:
                with contextlib.suppress(OSError):
                    client.close()
                continue
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _refuse(self, url: str, reason: str, host: str, port: int) -> None:
        with self._mu:
            self.refused.append((url, reason))
            self.refused_hostports.add((host.lower(), port))

    def _check(self, url: str) -> str | None:
        try:
            check_url(url, self.guard)
        except UrlRefusedError as e:
            return e.reason or "refused"
        return None

    def _resolve_checked(self, scheme: str, host: str, port: int, url: str):
        """Resolve once; every address must pass. Returns sockaddr list."""
        why = self._check(url)
        if why:
            self._refuse(url, why, host, port)
            return None
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            return []
        addrs = []
        for family, _t, _p, _c, sockaddr in infos:
            ip = sockaddr[0]
            ip_url = f"{scheme}://{_host_for_url(ip)}:{port}/"
            why = self._check(ip_url)
            if why:
                self._refuse(ip_url, why, host, port)
                return None
            addrs.append((family, sockaddr))
        return addrs

    def _connect(self, addrs) -> socket.socket | None:
        for family, sockaddr in addrs:
            s = socket.socket(family, socket.SOCK_STREAM)
            s.settimeout(self.connect_timeout)
            try:
                s.connect(sockaddr)  # the checked address — never re-resolved
            except OSError:
                s.close()
                continue
            if not self._track(s):
                s.close()
                return None
            return s
        return None

    def _read_head(self, client: socket.socket) -> tuple[bytes, bytes] | None:
        buf = b""
        while b"\r\n\r\n" not in buf:
            if len(buf) > 65536:
                return None
            chunk = client.recv(65536)
            if not chunk:
                return None
            buf += chunk
        head, rest = buf.split(b"\r\n\r\n", 1)
        return head, rest

    def _serve(self, client: socket.socket) -> None:
        upstream = None
        try:
            if not self._track(client):
                return
            client.settimeout(self.idle_timeout)
            got = self._read_head(client)
            if got is None:
                return
            head, rest = got
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3:
                return
            method, target, version = parts
            if method.upper() == "CONNECT":
                host, _, port_s = target.rpartition(":")
                host = host.strip("[]")
                try:
                    port = int(port_s)
                except ValueError:
                    return
                scheme = "http" if port == 80 else "https"
                url = f"{scheme}://{_host_for_url(host)}:{port}/"
                addrs = self._resolve_checked(scheme, host, port, url)
                if not addrs:
                    client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n"
                                   b"Connection: close\r\n\r\n")
                    return
                upstream = self._connect(addrs)
                if upstream is None:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n"
                                   b"Connection: close\r\n\r\n")
                    return
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
            else:
                sp = urlsplit(target)
                if sp.scheme.lower() != "http" or not sp.hostname:
                    return  # only absolute http:// URLs are proxied in the clear
                host = sp.hostname
                port = sp.port or 80
                addrs = self._resolve_checked("http", host, port, target)
                if not addrs:
                    return  # close without a response: the request FAILS (no page)
                upstream = self._connect(addrs)
                if upstream is None:
                    return
                path = sp.path or "/"
                if sp.query:
                    path += "?" + sp.query
                out = [f"{method} {path} {version}"]
                out += [h for h in lines[1:] if h.split(":", 1)[0].strip().lower() not in _HOP_HEADERS]
                out.append("Connection: close")
                upstream.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin-1") + rest)
            upstream.settimeout(self.idle_timeout)
            self._pipe(client, upstream)
        except OSError:
            pass
        finally:
            if upstream is not None:
                self._untrack(upstream)
            self._untrack(client)
            with self._mu:
                self._active -= 1

    def _pipe(self, a: socket.socket, b: socket.socket) -> None:
        sel = selectors.DefaultSelector()
        try:
            sel.register(a, selectors.EVENT_READ, b)
            sel.register(b, selectors.EVENT_READ, a)
            while True:
                events = sel.select(self.idle_timeout)
                if not events:
                    return  # idle
                for key, _ in events:
                    data = key.fileobj.recv(65536)
                    if not data:
                        return
                    key.data.sendall(data)
        finally:
            sel.close()


# ── headless browser (Playwright) ───────────────────────────────────────────

# Chromium flags for a guarded browser. The proxy settings make EVERY
# connection (HTTP, HTTPS, WebSocket, redirect hops, preconnects, reports)
# go through the guard proxy; `<-loopback>` stops Chromium's built-in
# "never proxy localhost" bypass, the commonest refused target.
GUARDED_CHROMIUM_ARGS = (
    "--proxy-bypass-list=<-loopback>",
    "--disable-quic",
    "--dns-prefetch-disable",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-features=WebRtcHideLocalIpsWithMdns,DnsOverHttps",
)

_NO_WEBRTC_JS = """(() => {
  for (const k of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'mozRTCPeerConnection',
                   'RTCDataChannel', 'RTCIceCandidate', 'RTCSessionDescription',
                   'RTCRtpSender', 'RTCRtpReceiver', 'RTCRtpTransceiver', 'RTCIceTransport',
                   'RTCDtlsTransport', 'RTCSctpTransport', 'RTCCertificate']) {
    try { Object.defineProperty(window, k, {value: undefined, writable: false, configurable: false}); }
    catch (e) { try { delete window[k]; } catch (e2) {} }
  }
})();"""


def guarded_launch_kwargs(proxy: GuardProxy) -> dict[str, Any]:
    """``BrowserType.launch`` kwargs that force all traffic through ``proxy``."""
    return {"proxy": {"server": proxy.url}, "args": list(GUARDED_CHROMIUM_ARGS)}


class GuardState:
    """Per-fetch record of what the guard refused (browser / yt-dlp)."""

    def __init__(self, proxy: GuardProxy | None = None) -> None:
        self.refused_navigation: str | None = None
        self.refused_subrequests: list[str] = []
        self.proxy = proxy
        # The page being extracted. Only ITS main-frame navigations are fatal;
        # a refused popup or iframe is blocked like any subresource.
        self.page: Any = None

    def raise_if_refused(self, url: str = "") -> None:
        if self.refused_navigation is not None:
            raise UrlRefusedError(self.refused_navigation, "page navigation refused")
        if self.proxy is not None and self.proxy.failed:
            raise UrlRefusedError(url, "guard proxy stopped during the fetch")


def install_browser_guard(context: Any, guard: Any = _UNSET,
                          proxy: GuardProxy | None = None) -> GuardState:
    """Check every request of a Playwright BrowserContext against the guard.

    No-op (returns an empty GuardState, installs nothing) when no guard is in
    effect, so unguarded behaviour is untouched.

    This is the full-URL layer: each request is checked before it is sent
    (``route``), WebSockets likewise, WebRTC is removed from every frame, and
    a failed main-frame navigation to a host the ``proxy`` refused is marked
    fatal. Redirect hops never reach ``route`` (Chromium follows them inside
    the network stack) — the guard proxy is what checks those, so a guarded
    browser must always be launched with :func:`guarded_launch_kwargs`.
    """
    g = get_url_guard() if guard is _UNSET else guard
    state = GuardState(proxy)
    if g is None:
        return state
    if not hasattr(context, "route_web_socket"):
        raise RuntimeError(
            "URL guard needs Playwright >= 1.48 (BrowserContext.route_web_socket)")

    def _is_main(request: Any) -> bool:
        try:
            frame = request.frame
            return (request.is_navigation_request() and frame.parent_frame is None
                    and (state.page is None or frame.page == state.page))
        except Exception:  # service-worker / detached-frame requests
            return False

    def _record(request: Any, url: str) -> None:
        if _is_main(request):
            if state.refused_navigation is None:
                state.refused_navigation = url
        else:
            state.refused_subrequests.append(url)

    def _handle(route: Any) -> None:
        request = route.request
        url = request.url
        if url.startswith(("data:", "blob:", "about:")):
            route.continue_()
            return
        if is_refused(url, g):
            _record(request, url)
            route.abort("blockedbyclient")
            return
        route.continue_()

    def _handle_ws(ws: Any) -> None:
        if is_refused(ws.url, g):
            # Never connect: an un-connected routed WebSocket sends nothing to
            # the server. (Calling ws.close() here deadlocks the sync API.)
            state.refused_subrequests.append(ws.url)
            return
        ws.connect_to_server()

    def _on_failed(request: Any) -> None:
        if proxy is None or not _is_main(request):
            return
        sp = urlsplit(request.url)
        port = sp.port or (443 if sp.scheme in ("https", "wss") else 80)
        if ((sp.hostname or "").lower(), port) in proxy.refused_hostports:
            if state.refused_navigation is None:
                state.refused_navigation = request.url

    context.add_init_script(_NO_WEBRTC_JS)
    context.route("**/*", _handle)
    context.route_web_socket("**/*", _handle_ws)
    context.on("requestfailed", _on_failed)
    return state


# ── yt-dlp ──────────────────────────────────────────────────────────────────

def guarded_ytdlp_opts(opts: dict[str, Any], proxy: GuardProxy) -> dict[str, Any]:
    """yt-dlp options for a guarded run: every request through ``proxy``,
    native downloaders only (ffmpeg / aria2c may not honour the proxy), and no
    post-processors (which may spawn ffmpeg)."""
    out = dict(opts)
    out["proxy"] = proxy.url
    out["external_downloader"] = {"default": "native"}
    out["hls_prefer_native"] = True
    out["postprocessors"] = []
    return out


def guard_ytdlp(ydl: Any, guard: Any = _UNSET, proxy: GuardProxy | None = None) -> GuardState:
    """Check every request URL a YoutubeDL instance makes (full-URL layer).

    Redirect hops are followed inside yt-dlp's request handlers; they are
    checked by the guard proxy the instance is configured with
    (:func:`guarded_ytdlp_opts`) before they are requested.

    yt-dlp wraps exceptions raised inside extraction in its own error types,
    so refusals are recorded on the returned GuardState; callers call
    ``raise_if_refused()`` after yt-dlp returns or raises.
    """
    g = get_url_guard() if guard is _UNSET else guard
    state = GuardState(proxy)
    if g is None:
        return state
    orig = ydl.urlopen

    def _refuse(url: str) -> None:
        if state.refused_navigation is None:
            state.refused_navigation = url

    def _urlopen(req: Any, *args: Any, **kwargs: Any) -> Any:
        url = req if isinstance(req, str) else getattr(req, "url", None) or req.get_full_url()
        if is_refused(url, g):
            _refuse(url)
            check_url(url, g)  # raises
        resp = orig(req, *args, **kwargs)
        final = getattr(resp, "url", None)
        if final and is_refused(final, g):
            _refuse(final)
            resp.close()
            check_url(final, g)  # raises
        return resp

    ydl.urlopen = _urlopen
    return state


def ytdlp_refusal(state: GuardState, proxy: GuardProxy | None, url: str) -> None:
    """After a guarded yt-dlp call: any proxy refusal or proxy failure is fatal
    (every yt-dlp request is essential to the extraction)."""
    state.raise_if_refused(url)
    if proxy is not None and proxy.refused:
        bad, why = proxy.refused[0]
        raise UrlRefusedError(bad, why)

