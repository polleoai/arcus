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
