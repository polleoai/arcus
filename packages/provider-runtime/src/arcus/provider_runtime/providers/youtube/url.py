"""YouTube URL → 11-char videoId parser."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "youtu.be", "m.youtube.com"}


def parse_youtube_url(raw: str) -> str:
    """Return the 11-char videoId from a YouTube URL.

    Raises ValueError for non-YouTube URLs, playlist-only URLs, and URLs
    without an extractable videoId.
    """
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Not a valid URL: {raw}")

    host = parsed.netloc.removeprefix("www.")
    if host not in _YOUTUBE_HOSTS:
        raise ValueError(f"Not a YouTube URL: {raw}")

    if parsed.path.startswith("/playlist"):
        raise ValueError(f"Playlist URLs not supported (v1 single-video only): {raw}")

    candidate: str | None = None
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/", 1)[0] or None
    elif parsed.path == "/watch":
        q = parse_qs(parsed.query)
        v = q.get("v")
        candidate = v[0] if v else None
    elif parsed.path.startswith("/shorts/"):
        candidate = parsed.path.removeprefix("/shorts/").split("/", 1)[0] or None
    elif parsed.path.startswith("/embed/"):
        candidate = parsed.path.removeprefix("/embed/").split("/", 1)[0] or None

    if not candidate or not _VIDEO_ID.match(candidate):
        raise ValueError(f"Could not extract videoId from URL: {raw}")

    return candidate
