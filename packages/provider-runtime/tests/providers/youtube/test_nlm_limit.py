from pathlib import Path

from arcus.provider_runtime.providers.youtube.nlm_limit import (
    FALLBACK_LIMIT,
    build_notebook_name,
    load_cached_limit,
    save_cached_limit,
    truncate_for_notebook_name,
)


class TestTruncate:
    def test_unchanged_when_within_budget(self) -> None:
        assert truncate_for_notebook_name("Short title", 200) == "Short title"

    def test_truncates_by_codepoint_not_byte(self) -> None:
        title = "你好世界" * 60
        out = truncate_for_notebook_name(title, 50)
        assert len(out) <= 50

    def test_prefers_natural_break_in_last_20_percent(self) -> None:
        title = "Distributed systems: lessons from production at scale"
        out = truncate_for_notebook_name(title, 30)
        assert ":" in out
        assert out.endswith("…")

    def test_falls_back_to_whole_word_boundary(self) -> None:
        title = "Distributed systems lessons from production scale"
        out = truncate_for_notebook_name(title, 25)
        assert out.endswith("…")
        prefix = out[:-1].rstrip()  # strip the … and any trailing space
        # Truncation must end on a word boundary: the prefix is a leading
        # substring of the title, and the next character in the title
        # (if any) is whitespace — never mid-word.
        assert title.startswith(prefix)
        next_idx = len(prefix)
        assert next_idx >= len(title) or title[next_idx].isspace()


class TestLimitCache:
    def test_load_returns_fallback_when_missing(self, tmp_path: Path) -> None:
        assert load_cached_limit(tmp_path / "missing.json") == FALLBACK_LIMIT

    def test_round_trip(self, tmp_path: Path) -> None:
        f = tmp_path / "limits.json"
        save_cached_limit(f, 250)
        assert load_cached_limit(f) == 250


class TestBuildNotebookName:
    def test_format_contains_required_fields(self) -> None:
        name = build_notebook_name(title="T", video_id="abc12345678", date="2026-05-17", limit=200)
        assert "arcus" in name
        assert "abc12345678" in name
        assert "2026-05-17" in name
        assert "T" in name

    def test_tag_in_brackets(self) -> None:
        name = build_notebook_name(
            tag="peitho", title="T", video_id="abc12345678", date="2026-05-17", limit=200
        )
        assert "[peitho]" in name
