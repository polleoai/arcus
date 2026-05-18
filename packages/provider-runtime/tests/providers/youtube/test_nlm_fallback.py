from arcus.provider_runtime.providers.youtube.nlm_fallback import (
    parse_transcript_to_segments,
)
from arcus.provider_runtime.types import Segment


def test_plain_text_one_segment_covers_duration() -> None:
    text = "Hello world. This is a transcript with no timestamps."
    out = parse_transcript_to_segments(text, 60_000)
    assert len(out) == 1
    assert out[0] == Segment(start_ms=0, end_ms=60_000, text=text)


def test_empty_input_returns_empty_list() -> None:
    assert parse_transcript_to_segments("", 1000) == []


def test_paragraph_split_distributes_across_duration() -> None:
    text = "Para 1.\n\nPara 2.\n\nPara 3."
    out = parse_transcript_to_segments(text, 90_000)
    assert len(out) == 3
    assert out[0].start_ms == 0
    assert out[2].end_ms == 90_000
    assert out[0].text == "Para 1."
    assert out[2].text == "Para 3."
