import json
from dataclasses import asdict, fields

from arcus.provider_runtime.types import (
    EXIT_CODES,
    ExtractionResult,
    Segment,
    SourceMetadata,
)


def test_exit_codes_are_int_constants() -> None:
    assert EXIT_CODES["SUCCESS"] == 0
    assert EXIT_CODES["INVALID_ARGS"] == 2
    assert EXIT_CODES["EXTRACTORS_EXHAUSTED"] == 30
    assert isinstance(EXIT_CODES["VIDEO_RESTRICTED"], int)


def test_segment_is_frozen_dataclass() -> None:
    s = Segment(start_ms=0, end_ms=1000, text="hi")
    assert s.start_ms == 0
    assert s.text == "hi"
    try:
        s.start_ms = 5  # type: ignore[misc]
    except Exception as e:
        assert "frozen" in str(e).lower() or "cannot assign" in str(e).lower()
    else:
        raise AssertionError("expected frozen-instance error")


def test_source_metadata_required_and_optional() -> None:
    m = SourceMetadata(
        source="https://example.com/x",
        source_id="abc",
        title="T",
        slug="t",
    )
    assert m.author is None
    assert m.duration_ms is None

    d = asdict(m)
    json.dumps(d)  # round-trips to JSON without TypeError


def test_extraction_result_has_no_children_field() -> None:
    """Enforces feedback-arcus-pure-download-layer: single-source only.

    Any future composite/multi-source feature must add the field back AND
    update this test — making the architectural choice explicit instead of
    drifting silently.
    """
    field_names = {f.name for f in fields(ExtractionResult)}
    assert "children" not in field_names, (
        "ExtractionResult.children was re-added. arcus is a single-source "
        "extraction layer; multi-source aggregation belongs in the consumer. "
        "See feedback-arcus-pure-download-layer."
    )
