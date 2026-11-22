"""X posts without a session (0.8.1 path A): X's public syndication endpoint.

Short posts come back complete; long posts keep what the endpoint gives and
are flagged truncated with a machine-readable reason; unavailable posts fail
clearly; the URL guard covers the endpoint. Never an empty success.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from arcus.provider_runtime import url_guard as ug
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers.html.html import HtmlProvider
from arcus.provider_runtime.types import EXIT_CODES

_SYND = "arcus.provider_runtime.providers.html.x_syndication.fetch_json"


def _xs():  # imported lazily so a missing module fails tests, not collection
    from arcus.provider_runtime.providers.html import x_syndication

    return x_syndication
URL = "https://x.com/jdpruettt/status/2124285595925217484"


def _ctx(tmp_path: Path) -> ExtractionContext:
    return ExtractionContext(out_dir=tmp_path, work_dir=tmp_path)


def _extract(tmp_path, payload, url=URL):
    p = HtmlProvider()
    with patch(_SYND, return_value=payload):
        return p.extract(p.matches(url), _ctx(tmp_path))


SHORT = {
    "__typename": "Tweet", "id_str": "2124285595925217484", "lang": "en",
    "text": "80-90% of AI incidents have no attacker &amp; no exploit -&gt;\n\n"
            "https://t.co/4VRb2l1v81 https://t.co/fkKxv5ONh0",
    "display_text_range": [0, 85],
    "entities": {"urls": [{"url": "https://t.co/4VRb2l1v81",
                           "expanded_url": "https://github.com/jdpru/cover-your-ais",
                           "display_url": "github.com/jdpru/cover-your-ais"}],
                 "media": [{"url": "https://t.co/fkKxv5ONh0"}]},
    "photos": [{"url": "https://pbs.twimg.com/media/G1.jpg"}],
    "mediaDetails": [{"type": "photo", "media_url_https": "https://pbs.twimg.com/media/G1.jpg",
                      "url": "https://t.co/fkKxv5ONh0"}],
    "user": {"screen_name": "jdpruettt", "name": "JD Pruett"},
    "created_at": "2026-11-21T23:35:28.000Z",
}


def test_short_post_complete_with_links_expanded_and_media_link_removed(tmp_path):
    r = _extract(tmp_path, SHORT)
    assert r.status == "success"
    assert r.text.startswith("80-90% of AI incidents have no attacker & no exploit ->")
    assert "https://github.com/jdpru/cover-your-ais" in r.text
    assert "t.co/" not in r.text
    ed = r.extractor_detail
    assert ed["extractor"] == "x_syndication" and ed["truncated"] is False
    assert "truncated_reason" not in ed
    assert ed["images"] == ["https://pbs.twimg.com/media/G1.jpg"]
    assert r.metadata.author == "@jdpruettt"


@pytest.mark.parametrize("signal", ["note_tweet", "self_link", "ellipsis"])
def test_long_post_flagged_truncated_no_session(tmp_path, signal):
    body = "A long post that goes on and on " * 8
    d = dict(SHORT, entities={"urls": []}, mediaDetails=[], photos=[])
    if signal == "note_tweet":
        d.update(text=body[:275] + "…", note_tweet={"id": "Tm90ZVR3ZWV0OjE="})
    elif signal == "self_link":
        d.update(text=body[:250] + "… https://t.co/self123",
                 entities={"urls": [{"url": "https://t.co/self123",
                                     "expanded_url": "https://twitter.com/i/web/status/2124285595925217484"}]})
    else:
        d.update(text=body[:279] + "…")
    r = _extract(tmp_path, d)
    assert r.status == "success" and r.text.strip()
    ed = r.extractor_detail
    assert ed["truncated"] is True
    assert ed["truncated_reason"] == "no_session"
    assert "t.co/self123" not in r.text and "i/web/status" not in r.text


def test_quoted_post_included(tmp_path):
    d = dict(SHORT, quoted_tweet={"text": "the original claim",
                                  "user": {"screen_name": "someone"}})
    r = _extract(tmp_path, d)
    assert "> @someone: the original claim" in r.text


@pytest.mark.parametrize("payload", [
    {"__typename": "TweetTombstone", "tombstone": {"text": {"text": "This Post is unavailable."}}},
    {},
])
def test_unavailable_post_fails_clearly(tmp_path, payload):
    r = _extract(tmp_path, payload)
    assert r.status == "failed"
    assert r.exit_code == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]
    assert "x post unavailable" in r.error.lower()


def test_tweet_id_parsing():
    assert _xs().tweet_id("https://x.com/a/status/123?s=20") == "123"
    assert _xs().tweet_id("https://twitter.com/a/statuses/456") == "456"
    assert _xs().tweet_id("https://x.com/i/web/status/789") == "789"
    assert _xs().tweet_id("https://x.com/a") is None


def test_fetch_json_goes_through_url_guard_and_endpoint(monkeypatch):
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(self.path)
            body = json.dumps(SHORT).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/tweet-result"
    x_syndication = _xs()
    monkeypatch.setattr(x_syndication, "SYNDICATION_URL", base)
    try:
        assert x_syndication.fetch_json("2124285595925217484")["id_str"] == "2124285595925217484"
        assert seen and seen[0].startswith("/tweet-result?id=2124285595925217484&")
        with ug.using_url_guard(lambda u: (_ for _ in ()).throw(PermissionError("no"))):
            with pytest.raises(ug.UrlRefusedError):
                x_syndication.fetch_json("1")
        assert len(seen) == 1  # the refused request never left
    finally:
        srv.shutdown()
