import os

import pytest

from arcus.provider_runtime.providers.youtube.ytdlp_adapter import (
    SubtitleTrack,
    parse_subtitles_from_info,
    select_track,
)


def test_parse_subtitles_empty_when_no_tracks() -> None:
    info = {"subtitles": {}, "automatic_captions": {}}
    assert parse_subtitles_from_info(info) == []


def test_parse_subtitles_flags_uploader() -> None:
    info = {
        "subtitles": {"en": [{"ext": "vtt"}], "zh-CN": [{"ext": "vtt"}]},
        "automatic_captions": {},
    }
    tracks = parse_subtitles_from_info(info)
    assert SubtitleTrack(lang="en", source="uploader") in tracks
    assert SubtitleTrack(lang="zh-CN", source="uploader") in tracks


def test_parse_subtitles_flags_auto_generated() -> None:
    info = {"subtitles": {}, "automatic_captions": {"en": [{"ext": "vtt"}]}}
    assert parse_subtitles_from_info(info) == [SubtitleTrack(lang="en", source="auto-generated")]


def test_parse_subtitles_prefers_uploader_when_both_for_same_lang() -> None:
    info = {
        "subtitles": {"en": [{"ext": "vtt"}]},
        "automatic_captions": {"en": [{"ext": "vtt"}]},
    }
    assert parse_subtitles_from_info(info) == [SubtitleTrack(lang="en", source="uploader")]


def test_select_track_honors_preferred_lang() -> None:
    tracks = [
        SubtitleTrack(lang="en", source="uploader"),
        SubtitleTrack(lang="zh-CN", source="uploader"),
    ]
    assert select_track(tracks, preferred="zh-CN") == SubtitleTrack(lang="zh-CN", source="uploader")


def test_select_track_prefers_uploader_en_over_auto_en() -> None:
    tracks = [
        SubtitleTrack(lang="en", source="auto-generated"),
        SubtitleTrack(lang="en", source="uploader"),
    ]
    assert select_track(tracks, preferred=None).source == "uploader"


def test_select_track_falls_back_to_first() -> None:
    tracks = [SubtitleTrack(lang="ja", source="auto-generated")]
    assert select_track(tracks, preferred=None) == tracks[0]


@pytest.mark.skipif(not os.environ.get("RUN_NET_TESTS"), reason="set RUN_NET_TESTS=1")
def test_fetch_metadata_smoke_real_network() -> None:
    from arcus.provider_runtime.providers.youtube.ytdlp_adapter import fetch_metadata

    meta = fetch_metadata("https://www.youtube.com/watch?v=jNQXAC9IVRw")  # "Me at the zoo"
    assert "zoo" in meta.title.lower()
    assert meta.duration_ms > 0


class _FakeYoutubeDL:
    """Drop-in stand-in for `yt_dlp.YoutubeDL` that returns a canned info dict.

    Lets us verify field-mapping (`info['description']` → `YtDlpMetadata.description`)
    without hitting the network. Implements only the `extract_info` +
    context-manager surface that `fetch_metadata` actually calls.
    """

    def __init__(self, info: dict) -> None:
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return self._info


def test_fetch_metadata_maps_description_field(mocker) -> None:
    from arcus.provider_runtime.providers.youtube import ytdlp_adapter

    mocker.patch.object(
        ytdlp_adapter, "YoutubeDL",
        lambda opts: _FakeYoutubeDL({
            "title": "A Video",
            "description": "The creator typed this in the description box.",
            "duration": 42.0,
            "subtitles": {},
            "automatic_captions": {},
        }),
    )
    meta = ytdlp_adapter.fetch_metadata("https://www.youtube.com/watch?v=abcdefghijk")
    assert meta.description == "The creator typed this in the description box."


def test_fetch_metadata_description_none_when_missing(mocker) -> None:
    """Empty-string description from yt-dlp normalizes to None, NOT "" —
    consumers can branch on `if meta.description:` without surprises."""
    from arcus.provider_runtime.providers.youtube import ytdlp_adapter

    mocker.patch.object(
        ytdlp_adapter, "YoutubeDL",
        lambda opts: _FakeYoutubeDL({
            "title": "A Video",
            "description": "",
            "duration": 42.0,
            "subtitles": {},
            "automatic_captions": {},
        }),
    )
    meta = ytdlp_adapter.fetch_metadata("https://www.youtube.com/watch?v=abcdefghijk")
    assert meta.description is None
