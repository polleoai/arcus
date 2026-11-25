"""X (Twitter) posts without a logged-in session: X's public syndication
endpoint — the one its embedded-post widget uses.

Short posts come back complete. Long posts ("note tweets") are cut by the
endpoint to roughly the first 280 characters; that is detected and reported
(`truncated`), never hidden. The request goes through arcus's URL guard.
"""

from __future__ import annotations

import html
import json
import math
import re
import urllib.parse
import urllib.request

from arcus.provider_runtime import url_guard
from arcus.provider_runtime.providers._shared.markdown_text import break_field_seps, escape_md_text

SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"

_STATUS_ID = re.compile(r"/(?:status|statuses)/(\d{1,25})(?:[/?#]|$)")
_TCO = re.compile(r"https?://t\.co/[A-Za-z0-9]+")


class XPostUnavailable(Exception):
    """The endpoint has no post for this id (deleted, protected, suspended)."""


def tweet_id(url: str) -> str | None:
    m = _STATUS_ID.search(urllib.parse.urlsplit(url).path + "/")
    return m.group(1) if m else None


def _token(tid: str) -> str:
    """The widget's token: ((id / 1e15) * π) in base 36, without zeros/dots."""
    x = (int(tid) / 1e15) * math.pi
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    whole, frac = int(x), x - int(x)
    out = ""
    while whole:
        whole, r = divmod(whole, 36)
        out = digits[r] + out
    out = (out or "0") + "."
    for _ in range(11):
        frac *= 36
        out += digits[int(frac)]
        frac -= int(frac)
    return re.sub(r"0+|\.", "", out) or "a"


def fetch_json(tid: str, timeout: float = 15) -> dict:
    """GET the syndication record for `tid` (guarded). 404 → {}."""
    q = urllib.parse.urlencode({"id": tid, "lang": "en", "token": _token(tid)})
    req = urllib.request.Request(f"{SYNDICATION_URL}?{q}",
                                 headers={"User-Agent": "Mozilla/5.0 (compatible; arcus)"})
    try:
        with url_guard.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise


def _self_link(expanded: str, tid: str) -> bool:
    return bool(re.search(r"(?:twitter|x)\.com/(?:i/web|[^/]+)/status(?:es)?/" + tid + r"\b",
                          expanded or ""))


def parse(d: dict, tid: str) -> dict:
    """Syndication record → {text, images, author, posted, truncated, signal}.

    Raises XPostUnavailable for tombstones / empty records."""
    if not d or d.get("__typename") == "TweetTombstone" or "text" not in d:
        reason = ((d.get("tombstone") or {}).get("text") or {}).get("text") if d else ""
        raise XPostUnavailable(reason or "no post returned for this id")

    text = d.get("text") or ""
    entities = d.get("entities") or {}
    truncated, signal = False, ""
    if d.get("note_tweet"):
        truncated, signal = True, "note_tweet"

    replacements: dict[str, str] = {}
    for u in entities.get("urls") or []:
        short, expanded = u.get("url"), u.get("expanded_url") or ""
        if not short:
            continue
        if _self_link(expanded, tid):
            replacements[short] = ""  # X's "show more" link back to the post
            truncated, signal = True, signal or "self_link"
        else:
            replacements[short] = expanded or short
    for m in (entities.get("media") or []) + (d.get("mediaDetails") or []):
        if m.get("url"):
            replacements[m["url"]] = ""  # media links; the images are listed separately

    text = _TCO.sub(lambda m: replacements.get(m.group(0), m.group(0)), text)
    text = escape_md_text(html.unescape(text).strip())  # decoded once, then text stays text (#23)
    if text.endswith("…") and not truncated:
        truncated, signal = True, "ellipsis"

    quoted = d.get("quoted_tweet") or {}
    if quoted.get("text"):
        who = escape_md_text((quoted.get("user") or {}).get("screen_name", ""))
        qtext = escape_md_text(html.unescape(_TCO.sub("", quoted["text"])).strip())
        text += f"\n\n> @{who}: {qtext}" if who else f"\n\n> {qtext}"
        text = break_field_seps(text)  # pieces joined: no "::" (#25)

    images = []
    for p in d.get("photos") or []:
        if p.get("url") and p["url"] not in images:
            images.append(p["url"])
    for m in d.get("mediaDetails") or []:
        u = m.get("media_url_https")
        if m.get("type") == "photo" and u and u not in images:
            images.append(u)

    user = d.get("user") or {}
    created = d.get("created_at") or ""
    return {
        "text": re.sub(r"[ \t]+\n", "\n", text).strip(),
        "images": images,
        "author": f"@{user['screen_name']}" if user.get("screen_name") else None,
        "posted": created[:10] if re.match(r"\d{4}-\d{2}-\d{2}", created) else None,
        "truncated": truncated,
        "signal": signal,
    }
