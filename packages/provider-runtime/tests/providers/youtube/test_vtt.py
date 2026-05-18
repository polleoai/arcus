from arcus.provider_runtime.providers.youtube.vtt import build_paragraphs, parse_vtt
from arcus.provider_runtime.types import Segment


SAMPLE = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:03.200
Today we're going to look

00:00:03.200 --> 00:00:07.100
at how Harness lets agents
fully automate making

00:00:07.500 --> 00:00:10.000
knowledge-explainer videos.
"""


def test_parse_three_cues_into_segments() -> None:
    segments = parse_vtt(SAMPLE)
    assert len(segments) == 3
    assert segments[0] == Segment(start_ms=0, end_ms=3200, text="Today we're going to look")
    assert segments[1].end_ms == 7100


def test_joins_multiline_cue_text() -> None:
    segments = parse_vtt(SAMPLE)
    assert segments[1].text == "at how Harness lets agents fully automate making"


def test_ignores_webvtt_header_block() -> None:
    segments = parse_vtt(SAMPLE)
    assert all("WEBVTT" not in s.text for s in segments)
    assert all("Kind" not in s.text for s in segments)


def test_handles_hhmmss_and_mmss_timestamps() -> None:
    vtt = "WEBVTT\n\n01:02:03.456 --> 01:02:05.789\nhello\n"
    segments = parse_vtt(vtt)
    assert segments[0].start_ms == 3_723_456
    assert segments[0].end_ms == 3_725_789


def test_empty_input_returns_empty_list() -> None:
    assert parse_vtt("WEBVTT\n\n") == []


def test_strips_cue_position_attributes() -> None:
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000 align:start position:0%\nhello\n"
    segments = parse_vtt(vtt)
    assert segments[0].text == "hello"


def test_deduplicates_rolling_captions() -> None:
    vtt = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.000\nhello world\n\n"
        "00:00:01.000 --> 00:00:02.000\nhello world\n"
    )
    segments = parse_vtt(vtt)
    assert len(segments) == 1
    assert segments[0].end_ms == 2000


def test_build_paragraphs_splits_on_long_gaps() -> None:
    segments = [
        Segment(start_ms=0, end_ms=1000, text="First sentence."),
        Segment(start_ms=1100, end_ms=2000, text="Same paragraph still."),
        Segment(start_ms=4000, end_ms=5000, text="New paragraph after long gap."),
    ]
    paragraphs = build_paragraphs(segments)
    assert paragraphs == [
        "First sentence. Same paragraph still.",
        "New paragraph after long gap.",
    ]
