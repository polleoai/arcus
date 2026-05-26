from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers.youtube import youtube as _yt_module
from arcus.provider_runtime.providers.youtube.youtube import YouTubeProvider
from arcus.provider_runtime.providers.youtube.ytdlp_adapter import (
    FetchCaptionsResult,
    SubtitleTrack,
    YtDlpMetadata,
)
from arcus.provider_runtime.types import EXIT_CODES


VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello there.\n"


@pytest.fixture(autouse=True)
def _clear_module_metadata_cache():
    """The youtube provider holds a per-URL metadata cache to avoid
    redundant yt-dlp fetches across predict_slug + extract. The cache
    must be reset between tests so mocks from one test don't leak into
    another that reuses the same URL."""
    _yt_module._METADATA_CACHE.clear()
    yield
    _yt_module._METADATA_CACHE.clear()


def make_context(tmp_path: Path) -> ExtractionContext:
    work = tmp_path / "work"
    work.mkdir()
    return ExtractionContext(out_dir=tmp_path, work_dir=work, factory=None)


def test_matches_youtube_url() -> None:
    p = YouTubeProvider()
    det = p.matches("https://www.youtube.com/watch?v=abcdefghijk")
    assert det is not None
    assert det.kind == "youtube"
    assert det.source_id == "abcdefghijk"


def test_does_not_match_non_youtube() -> None:
    p = YouTubeProvider()
    assert p.matches("https://vimeo.com/123") is None


def test_captions_happy_path(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch(
        "arcus.provider_runtime.providers.youtube.youtube.fetch_metadata",
        return_value=YtDlpMetadata(
            title="Sample",
            channel="Ch",
            duration_ms=60_000,
            posted="2025-01-15",
            language=None,
            subtitle_tracks=[SubtitleTrack(lang="en", source="uploader")],
        ),
    )
    mocker.patch(
        "arcus.provider_runtime.providers.youtube.youtube.fetch_captions",
        return_value=FetchCaptionsResult(
            vtt_content=VTT,
            selected_track=SubtitleTrack(lang="en", source="uploader"),
        ),
    )

    p = YouTubeProvider()
    det = p.matches("https://www.youtube.com/watch?v=abcdefghijk")
    assert det is not None
    result = p.extract(det, make_context(tmp_path))

    assert result.status == "success"
    assert result.kind == "youtube"
    assert result.extractor_detail["caption_source"] == "uploader"
    assert "Hello there." in result.text


def test_youtube_emits_progress_on_captions_path(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch(
        "arcus.provider_runtime.providers.youtube.youtube.fetch_metadata",
        return_value=YtDlpMetadata(
            title="Sample",
            channel="Ch",
            duration_ms=60_000,
            posted="2025-01-15",
            language=None,
            subtitle_tracks=[SubtitleTrack(lang="en", source="uploader")],
        ),
    )
    mocker.patch(
        "arcus.provider_runtime.providers.youtube.youtube.fetch_captions",
        return_value=FetchCaptionsResult(
            vtt_content=VTT,
            selected_track=SubtitleTrack(lang="en", source="uploader"),
        ),
    )

    stages: list[str] = []
    ctx = make_context(tmp_path)
    ctx.emit_progress = stages.append

    p = YouTubeProvider()
    det = p.matches("https://www.youtube.com/watch?v=abcdefghijk")
    assert det is not None
    result = p.extract(det, ctx)

    assert result.status == "success"
    assert "extracting" in stages
    assert stages.index("fetching") < stages.index("extracting")


def test_no_captions_no_nlm_auth_returns_failed(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch(
        "arcus.provider_runtime.providers.youtube.youtube.fetch_metadata",
        return_value=YtDlpMetadata(
            title="Sample",
            channel=None,
            duration_ms=60_000,
            posted=None,
            language=None,
            subtitle_tracks=[],
        ),
    )
    from arcus.provider_runtime.providers.youtube.nlm_fallback import NlmNotAuthenticatedError

    mocker.patch(
        "arcus.provider_runtime.providers.youtube.youtube.check_auth",
        side_effect=NlmNotAuthenticatedError("not authenticated"),
    )

    p = YouTubeProvider()
    det = p.matches("https://www.youtube.com/watch?v=abcdefghijk")
    assert det is not None
    result = p.extract(det, make_context(tmp_path))

    assert result.status == "failed"
    assert result.exit_code == EXIT_CODES["TOOL_NOT_AUTHENTICATED"]
    assert "not authenticated" in (result.error or "").lower()
