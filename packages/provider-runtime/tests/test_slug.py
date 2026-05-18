from pathlib import Path

import pytest

from arcus.provider_runtime.slug import disambiguate, make_slug


class TestMakeSlug:
    def test_ascii_title_passes_through(self) -> None:
        assert make_slug("Hello World") == "hello-world"

    def test_punctuation_collapsed_to_single_dash(self) -> None:
        assert make_slug("Hello, World! How's life?") == "hello-world-how-s-life"

    def test_unicode_diacritics_stripped(self) -> None:
        assert make_slug("Café Münch") == "cafe-munch"

    def test_chinese_falls_to_pinyin_or_empty(self) -> None:
        # Non-Latin scripts get stripped by NFKD; result is empty/dashes.
        # The fallback is the source_id (handled by disambiguate); make_slug
        # itself may return a short or empty string here.
        out = make_slug("你好世界")
        assert out == "" or out.isascii()

    def test_truncation_at_whole_word(self) -> None:
        title = "this is a fairly long title with many words about distributed systems and consensus"
        out = make_slug(title, max_len=30)
        assert len(out) <= 30
        assert not out.endswith("-")
        assert "and-consensus" not in out  # truncated before this

    def test_leading_trailing_dashes_trimmed(self) -> None:
        assert make_slug("---hello---") == "hello"

    def test_runs_of_separators_collapsed(self) -> None:
        assert make_slug("a   b___c---d") == "a-b-c-d"


class TestDisambiguate:
    def test_no_collision_returns_bare_slug(self, tmp_path: Path) -> None:
        assert disambiguate("hello", "abc12345678", tmp_path) == "hello"

    def test_collision_appends_short_id(self, tmp_path: Path) -> None:
        (tmp_path / "hello.md").write_text("existing")
        out = disambiguate("hello", "abc12345678", tmp_path)
        assert out == "hello--abc12345"

    def test_empty_slug_falls_back_to_source_id(self, tmp_path: Path) -> None:
        assert disambiguate("", "abc12345678", tmp_path) == "abc12345678"

    def test_collision_with_short_id_also_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "hello.md").write_text("e")
        (tmp_path / "hello--abc12345.md").write_text("e")
        # second collision: keep appending — uses full source_id
        out = disambiguate("hello", "abc12345678", tmp_path)
        assert out == "hello--abc12345678"
