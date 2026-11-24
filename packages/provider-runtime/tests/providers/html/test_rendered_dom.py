"""#13: the html2md stage converts the browser-rendered DOM, never a second static fetch."""

from __future__ import annotations

import shutil

import pytest

from arcus.provider_runtime import url_guard
from arcus.provider_runtime.providers.html import _athena_fetch_page as fp

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="html2md needs node")

# An address nothing listens on: any fetch of it fails, so output can only
# come from the HTML the browser handed over.
_URL = "http://127.0.0.1:9/docs/page"
_RENDERED = (
    "<html><head><title>Rendered</title></head><body><main>"
    "<h1>Hydrated article</h1>"
    + "<p>Client-side rendered paragraph that only exists after JavaScript ran.</p>" * 8
    + '<p><a href="./next">Next page</a> <img src="img/fig.png" alt="fig"></p>'
    "</main></body></html>"
)


class _FakePage:
    """The Playwright calls _render_via_html2md makes, with a fixed rendered DOM."""

    url = "http://127.0.0.1:9/docs/page"

    def goto(self, *a, **k):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def evaluate(self, script):
        return _RENDERED if "outerHTML" in script else None


@pytest.fixture
def no_static_fetch(monkeypatch):
    """Every html2md call converts HTML handed to it: `--base-url`, never `--url`."""
    import subprocess

    real, calls = subprocess.run, []

    def recording(argv, *a, **k):
        if any("html2md" in str(x) for x in argv):
            calls.append(list(argv))
        return real(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", recording)
    yield
    assert calls, "html2md was not run"
    for argv in calls:
        assert "--url" not in argv and "--base-url" in argv, argv


@pytest.mark.parametrize("guarded", [False, True])
def test_html2md_converts_the_rendered_dom(no_static_fetch, guarded):
    if guarded:
        with url_guard.using_url_guard(lambda u: None):
            md = fp._render_via_html2md(_FakePage(), _URL)
    else:
        md = fp._render_via_html2md(_FakePage(), _URL)
    assert md is not None
    assert "Client-side rendered paragraph" in md
    assert "Hydrated article" in md


def test_relative_links_resolve_against_the_rendered_page(no_static_fetch):
    md = fp._render_via_html2md(_FakePage(), _URL)
    assert md is not None
    assert "(http://127.0.0.1:9/docs/next)" in md
    assert "(http://127.0.0.1:9/docs/img/fig.png)" in md


def test_relative_links_use_the_final_url_after_redirects(no_static_fetch):
    class _Redirected(_FakePage):
        url = "http://127.0.0.1:9/moved/here"

    md = fp._render_via_html2md(_Redirected(), _URL)
    assert md is not None
    assert "(http://127.0.0.1:9/moved/next)" in md
