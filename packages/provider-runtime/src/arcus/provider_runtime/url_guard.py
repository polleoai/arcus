"""Caller-supplied URL policy for every network fetch arcus makes.

arcus stays policy-free: it does not decide which hosts are allowed. A caller
that hands arcus untrusted URLs installs a guard — a callable
``guard(url: str) -> None`` that raises to refuse — and arcus calls it on
*every* URL it is about to fetch, not just the first one:

- the input URL, and every HTTP redirect hop of every urllib fetch
  (downloads, HEAD probes, t.co resolution, static HTML fetches) and the
  final URL after redirects;
- every request the headless browser makes — main-frame navigations
  (including redirect hops, meta refresh and script-driven navigations),
  frames, subresources, popups and WebSockets;
- every request yt-dlp makes, plus the final URL after redirects.

Why module-level (``set_url_guard``) and not a field on ExtractionContext:
some fetches happen outside ``extract()`` — ``Provider.predict_slug`` may
fetch metadata, and the vendored fetch helpers are plain functions called
by consumers directly. A process-wide setting covers all of them.

With no guard installed, behaviour is identical to earlier releases.

A refused *page* (the input URL, a redirect of it, or a main-frame
navigation) raises :class:`UrlRefusedError`; providers never turn it into a
silent empty result. A refused *subresource* in the browser (an image,
iframe, script, WebSocket) is blocked and the page is still extracted.

Known residual: the guard sees URLs, not sockets. Between the guard's own
DNS check and the actual connect, a hostile DNS server can re-point the name
(a DNS-rebinding race). Callers that need to close that window can pass
``urlopen=`` — an opener that pins the resolved address — which arcus then
uses for all of its urllib fetches. The browser and yt-dlp resolve names
themselves, so the race remains for those paths.
"""

from __future__ import annotations

import contextlib
import shutil
import threading
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urljoin

UrlGuard = Callable[[str], None]
UrlOpen = Callable[..., Any]

__all__ = [
    "UrlGuard",
    "UrlRefusedError",
    "check_url",
    "download",
    "get_url_guard",
    "set_url_guard",
    "using_url_guard",
    "urlopen",
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


_lock = threading.Lock()
_guard: UrlGuard | None = None
_urlopen_override: UrlOpen | None = None


def set_url_guard(guard: UrlGuard | None, *, urlopen: UrlOpen | None = None) -> None:
    """Install (or, with ``None``, remove) the process-wide URL guard.

    ``urlopen`` optionally replaces arcus's own urllib opener. It must accept
    ``(request, timeout=...)`` like ``urllib.request.urlopen`` and return a
    response with ``.geturl()``/``.headers``/``.read()``; it is then
    responsible for checking redirect hops itself (arcus still checks the
    initial and final URLs).
    """
    global _guard, _urlopen_override
    with _lock:
        _guard = guard
        _urlopen_override = urlopen if guard is not None else None


def get_url_guard() -> UrlGuard | None:
    return _guard


@contextlib.contextmanager
def using_url_guard(guard: UrlGuard | None, *, urlopen: UrlOpen | None = None) -> Iterator[None]:
    """Scope a guard to a ``with`` block, restoring the previous one after."""
    prev = (_guard, _urlopen_override)
    set_url_guard(guard, urlopen=urlopen)
    try:
        yield
    finally:
        set_url_guard(prev[0], urlopen=prev[1])


def check_url(url: str) -> None:
    """Run the guard on ``url``. No-op when no guard is installed.

    Any exception the guard raises becomes :class:`UrlRefusedError`.
    """
    guard = _guard
    if guard is None:
        return
    try:
        guard(url)
    except UrlRefusedError:
        raise
    except Exception as e:  # the guard's own refusal type is caller-defined
        raise UrlRefusedError(url, str(e) or type(e).__name__) from e


def is_refused(url: str) -> bool:
    try:
        check_url(url)
    except UrlRefusedError:
        return True
    return False


# ── urllib ──────────────────────────────────────────────────────────────────

class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-checks every redirect hop BEFORE urllib requests it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        check_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(req: urllib.request.Request | str, timeout: float | None = None) -> Any:
    """``urllib.request.urlopen`` with the guard on every hop.

    Raises :class:`UrlRefusedError` on a refused URL; otherwise raises what
    urllib raises.
    """
    kwargs: dict[str, Any] = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if _guard is None:  # unguarded: exactly the pre-0.7.1 call
        return urllib.request.urlopen(req, **kwargs)
    if isinstance(req, str):
        req = urllib.request.Request(req)
    check_url(req.full_url)
    override = _urlopen_override
    if override is not None:
        resp = override(req, **kwargs)
    else:
        resp = urllib.request.build_opener(_GuardedRedirectHandler).open(req, **kwargs)
    try:
        check_url(resp.geturl())
    except UrlRefusedError:
        resp.close()
        raise
    return resp


def download(url: str, dest: str, timeout: float | None = None) -> None:
    """Guarded replacement for ``urllib.request.urlretrieve(url, dest)``."""
    if _guard is None:  # unguarded: exactly the pre-0.7.1 call
        urllib.request.urlretrieve(url, dest)
        return
    with urlopen(url, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


# ── headless browser (Playwright) ───────────────────────────────────────────

class GuardState:
    """Per-fetch record of what the guard refused (browser / yt-dlp)."""

    def __init__(self) -> None:
        self.refused_navigation: str | None = None
        self.refused_subrequests: list[str] = []
        # The page being extracted. Only ITS main-frame navigations are fatal;
        # a refused popup or iframe is blocked like any subresource.
        self.page: Any = None

    def raise_if_refused(self) -> None:
        if self.refused_navigation is not None:
            raise UrlRefusedError(self.refused_navigation, "page navigation refused")


def install_browser_guard(context: Any) -> GuardState:
    """Route every request of a Playwright BrowserContext through the guard.

    No-op (returns an empty GuardState, installs no route) when no guard is
    set, so unguarded behaviour is untouched.

    Playwright's route handler is NOT invoked for HTTP redirect hops when a
    request is continued — the browser follows them internally. So guarded
    requests are fetched by the handler itself with ``max_redirects=0``; a
    3xx's Location is checked before the response is handed back, and the
    browser's follow-up request for it comes back through this route.
    """
    state = GuardState()
    if _guard is None:
        return state

    def _record(request: Any, url: str) -> None:
        try:
            frame = request.frame
            main = (
                request.is_navigation_request()
                and frame.parent_frame is None
                and (state.page is None or frame.page == state.page)
            )
        except Exception:  # service-worker / detached-frame requests
            main = False
        if main:
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
        if is_refused(url):
            _record(request, url)
            route.abort("blockedbyclient")
            return
        try:
            resp = route.fetch(max_redirects=0)
        except Exception:
            route.abort("failed")
            return
        location = resp.headers.get("location")
        if 300 <= resp.status < 400 and location:
            target = urljoin(url, location)
            if is_refused(target):
                _record(request, target)
                route.abort("blockedbyclient")
                return
        route.fulfill(response=resp)

    def _handle_ws(ws: Any) -> None:
        if is_refused(ws.url):
            # Never connect: an un-connected routed WebSocket sends nothing to
            # the server. (Calling ws.close() here deadlocks the sync API.)
            state.refused_subrequests.append(ws.url)
            return
        ws.connect_to_server()

    context.route("**/*", _handle)
    if hasattr(context, "route_web_socket"):
        context.route_web_socket("**/*", _handle_ws)
    return state


# ── yt-dlp ──────────────────────────────────────────────────────────────────

def guard_ytdlp(ydl: Any) -> GuardState:
    """Make a YoutubeDL instance check every request URL and final URL.

    yt-dlp follows redirects inside its own request handlers, so a redirect
    hop is checked by the response's final URL (the response is discarded and
    the extraction fails) rather than before it is requested. The YouTube
    provider only accepts YouTube URLs, so this is defence in depth.

    yt-dlp wraps exceptions raised inside extraction in its own error types,
    so refusals are also recorded on the returned GuardState; callers call
    ``raise_if_refused()`` after yt-dlp returns or raises.
    """
    state = GuardState()
    if _guard is None:
        return state
    orig = ydl.urlopen

    def _refuse(url: str) -> None:
        if state.refused_navigation is None:
            state.refused_navigation = url

    def _urlopen(req: Any, *args: Any, **kwargs: Any) -> Any:
        url = req if isinstance(req, str) else getattr(req, "url", None) or req.get_full_url()
        if is_refused(url):
            _refuse(url)
            check_url(url)  # raises
        resp = orig(req, *args, **kwargs)
        final = getattr(resp, "url", None)
        if final and is_refused(final):
            _refuse(final)
            resp.close()
            check_url(final)  # raises
        return resp

    ydl.urlopen = _urlopen
    return state
