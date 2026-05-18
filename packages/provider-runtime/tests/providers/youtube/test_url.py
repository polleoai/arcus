import pytest

from arcus.provider_runtime.providers.youtube.url import parse_youtube_url


class TestParseYoutubeUrl:
    def test_watch_v_query(self) -> None:
        assert parse_youtube_url("https://youtube.com/watch?v=0hM2SkHZ2UU") == "0hM2SkHZ2UU"

    def test_www_subdomain(self) -> None:
        assert parse_youtube_url("https://www.youtube.com/watch?v=0hM2SkHZ2UU") == "0hM2SkHZ2UU"

    def test_youtu_be_short(self) -> None:
        assert parse_youtube_url("https://youtu.be/0hM2SkHZ2UU") == "0hM2SkHZ2UU"

    def test_shorts_path(self) -> None:
        assert parse_youtube_url("https://youtube.com/shorts/0hM2SkHZ2UU") == "0hM2SkHZ2UU"

    def test_embed_path(self) -> None:
        assert parse_youtube_url("https://youtube.com/embed/0hM2SkHZ2UU") == "0hM2SkHZ2UU"

    def test_strips_extra_query(self) -> None:
        url = "https://youtube.com/watch?v=0hM2SkHZ2UU&t=42s&feature=share"
        assert parse_youtube_url(url) == "0hM2SkHZ2UU"

    def test_rejects_playlist(self) -> None:
        with pytest.raises(ValueError, match="[Pp]laylist"):
            parse_youtube_url("https://youtube.com/playlist?list=PLxxx")

    def test_rejects_non_youtube(self) -> None:
        with pytest.raises(ValueError, match="[Nn]ot a YouTube"):
            parse_youtube_url("https://vimeo.com/12345")

    def test_rejects_malformed(self) -> None:
        with pytest.raises(ValueError):
            parse_youtube_url("not a url")

    def test_rejects_url_without_video_id(self) -> None:
        with pytest.raises(ValueError, match="videoId|video id"):
            parse_youtube_url("https://youtube.com/")
