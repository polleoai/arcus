"""yt-dlp Python API wrapper for metadata + caption fetching."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


@dataclass(frozen=True)
class SubtitleTrack:
    lang: str
    source: Literal["uploader", "auto-generated"]


@dataclass(frozen=True)
class YtDlpMetadata:
    title: str
    channel: str | None
    duration_ms: int
    posted: str | None
    language: str | None
    subtitle_tracks: list[SubtitleTrack]


class RestrictedVideoError(Exception):
    """Raised for private / age-locked / region-locked videos."""


class YtDlpExtractionError(Exception):
    """Raised when yt-dlp fails for non-restriction reasons."""


_RESTRICTED_HINTS = (
    "private",
    "age",
    "members-only",
    "sign in",
    "unavailable in your country",
)


def fetch_metadata(url: str) -> YtDlpMetadata:
    """Fetch metadata via yt-dlp's Python API (no download)."""
    opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        msg = str(e).lower()
        if any(hint in msg for hint in _RESTRICTED_HINTS):
            raise RestrictedVideoError(str(e)) from e
        raise YtDlpExtractionError(str(e)) from e

    if not isinstance(info, dict):
        raise YtDlpExtractionError("yt-dlp returned non-dict info")

    upload_date = info.get("upload_date")
    posted: str | None = None
    if isinstance(upload_date, str) and len(upload_date) == 8:
        posted = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"

    return YtDlpMetadata(
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader"),
        duration_ms=int(round((info.get("duration") or 0) * 1000)),
        posted=posted,
        language=info.get("language"),
        subtitle_tracks=parse_subtitles_from_info(info),
    )


def parse_subtitles_from_info(info: dict[str, Any]) -> list[SubtitleTrack]:
    """Flatten yt-dlp's `subtitles` + `automatic_captions` into a track list.

    Uploader tracks win over auto-generated when the same lang exists in both.
    """
    uploader = list((info.get("subtitles") or {}).keys())
    auto = list((info.get("automatic_captions") or {}).keys())

    seen: set[str] = set()
    out: list[SubtitleTrack] = []
    for lang in uploader:
        if lang not in seen:
            out.append(SubtitleTrack(lang=lang, source="uploader"))
            seen.add(lang)
    for lang in auto:
        if lang not in seen:
            out.append(SubtitleTrack(lang=lang, source="auto-generated"))
            seen.add(lang)
    return out


def select_track(tracks: list[SubtitleTrack], preferred: str | None) -> SubtitleTrack:
    """Pick the best subtitle track: preferred lang > uploader+en > any uploader > en > first."""
    if not tracks:
        raise ValueError("No subtitle tracks to select from")
    if preferred:
        for t in tracks:
            if t.lang == preferred:
                return t
    for t in tracks:
        if t.lang == "en" and t.source == "uploader":
            return t
    for t in tracks:
        if t.source == "uploader":
            return t
    for t in tracks:
        if t.lang == "en":
            return t
    return tracks[0]


@dataclass(frozen=True)
class FetchCaptionsResult:
    vtt_content: str
    selected_track: SubtitleTrack


def fetch_captions(
    url: str,
    video_id: str,
    preferred_lang: str | None,
    available_tracks: list[SubtitleTrack],
    work_dir: Path,
) -> FetchCaptionsResult:
    """Download the selected caption track via yt-dlp's Python API."""
    selected = select_track(available_tracks, preferred_lang)

    opts: dict[str, Any] = {
        "skip_download": True,
        "writesubtitles": selected.source == "uploader",
        "writeautomaticsub": selected.source == "auto-generated",
        "subtitleslangs": [selected.lang],
        "subtitlesformat": "vtt",
        "outtmpl": str(work_dir / f"{video_id}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    with YoutubeDL(opts) as ydl:
        ydl.download([url])

    matches = list(work_dir.glob(f"{video_id}*.vtt"))
    if not matches:
        raise YtDlpExtractionError("yt-dlp did not produce a .vtt file")

    vtt_content = matches[0].read_text(encoding="utf-8")
    return FetchCaptionsResult(vtt_content=vtt_content, selected_track=selected)
