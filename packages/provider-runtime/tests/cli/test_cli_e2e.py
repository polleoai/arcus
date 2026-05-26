"""E2E CLI tests for HTML + PDF dispatch.

These exercise `arcus.cli.main` end-to-end through the real factory; they
mock the provider's extract() to avoid network. Cache-hit and --force are
tested against the real on-disk writer.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from arcus.cli.main import main
from arcus.provider_runtime.types import ExtractionResult, SourceMetadata


# CLI tests live at packages/provider-runtime/tests/cli/; fixtures live at
# packages/provider-runtime/tests/providers/<kind>/fixtures/ → ../providers/...
FIXTURE_PDF = (
    Path(__file__).parent.parent / "providers" / "pdf" / "fixtures" / "small.pdf"
)

FIXTURE_DOCS_DIR = (
    Path(__file__).parent.parent / "providers" / "docs" / "fixtures"
)


def _success_result(*, kind: str, source: str, slug: str, text: str) -> ExtractionResult:
    return ExtractionResult(
        status="success",
        kind=kind,
        extractor_detail={"extractor": "mocked"},
        metadata=SourceMetadata(
            source=source, source_id=source, title=f"Title for {slug}", slug=slug,
        ),
        text=text,
        segments=[],
        extracted_at="2026-05-18T00:00:00+00:00",
    )


# ── HTML dispatch ───────────────────────────────────────────────────


def test_cli_extracts_html_url(tmp_path: Path) -> None:
    url = "https://example.com/article"
    with patch(
        "arcus.provider_runtime.providers.html.html.HtmlProvider.extract",
        return_value=_success_result(
            kind="html", source=url, slug="example-com-article",
            text="# Article\n\nBody.",
        ),
    ):
        exit_code = main([url, "--out", str(tmp_path)])

    assert exit_code == 0
    assert (tmp_path / "example-com-article.md").exists()
    assert (tmp_path / "example-com-article.json").exists()
    body = (tmp_path / "example-com-article.md").read_text(encoding="utf-8")
    assert "Body." in body


# ── PDF dispatch (real extraction against fixture) ──────────────────


def test_cli_extracts_local_pdf(tmp_path: Path) -> None:
    """Real PdfProvider against the fixture PDF — no mocks."""
    assert FIXTURE_PDF.exists(), f"missing fixture: {FIXTURE_PDF}"
    exit_code = main([str(FIXTURE_PDF), "--out", str(tmp_path)])
    assert exit_code == 0
    assert (tmp_path / "small.md").exists()
    assert (tmp_path / "small.json").exists()


@pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx", "epub"])
def test_cli_extracts_local_docs(ext, tmp_path: Path) -> None:
    """Real DocsProvider against each fixture format — no mocks."""
    fixture = FIXTURE_DOCS_DIR / f"small.{ext}"
    assert fixture.exists(), f"missing fixture: {fixture}"
    exit_code = main([str(fixture), "--out", str(tmp_path)])
    assert exit_code == 0
    assert (tmp_path / "small.md").exists()
    assert (tmp_path / "small.json").exists()
    body = (tmp_path / "small.md").read_text(encoding="utf-8")
    assert "Body text for DocsProvider testing." in body


def test_cli_extracts_remote_pdf(tmp_path: Path) -> None:
    """Remote PDF via mocked urlretrieve + HEAD; real extractor."""
    url = "https://example.com/paper.pdf"

    def fake_urlretrieve(remote_url, dest_path):
        shutil.copy(FIXTURE_PDF, dest_path)
        return dest_path, {"Content-Type": "application/pdf"}

    with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve), \
         patch(
             "arcus.provider_runtime.providers.pdf.pdf.PdfProvider._head_content_type",
             return_value="application/pdf",
         ):
        exit_code = main([url, "--out", str(tmp_path)])

    assert exit_code == 0
    assert (tmp_path / "paper.md").exists()


# ── Cache hit + --force ─────────────────────────────────────────────


def test_cli_cache_hit_skips_second_run(tmp_path: Path) -> None:
    url = "https://example.com/article"
    success = _success_result(
        kind="html", source=url, slug="example-com-article", text="# A\n\nB.",
    )
    with patch(
        "arcus.provider_runtime.providers.html.html.HtmlProvider.extract",
        return_value=success,
    ) as ex:
        # First run extracts
        assert main([url, "--out", str(tmp_path)]) == 0
        assert ex.call_count == 1
        # Second run is a cache hit — extract() NOT called again
        assert main([url, "--out", str(tmp_path)]) == 0
        assert ex.call_count == 1, "cache hit should skip extract()"


def test_cli_force_bypasses_cache(tmp_path: Path) -> None:
    url = "https://example.com/article"
    success = _success_result(
        kind="html", source=url, slug="example-com-article", text="# A\n\nB.",
    )
    with patch(
        "arcus.provider_runtime.providers.html.html.HtmlProvider.extract",
        return_value=success,
    ) as ex:
        assert main([url, "--out", str(tmp_path)]) == 0
        assert main([url, "--out", str(tmp_path), "--force"]) == 0
        assert ex.call_count == 2, "--force should re-extract"


# ── forced provider (--provider) ────────────────────────────────────


def test_cli_forced_provider_no_match_exits_11(tmp_path: Path) -> None:
    """`--provider html` against a local .pdf path: html won't match → exit 11."""
    exit_code = main(["--provider", "html", "/tmp/x.pdf", "--out", str(tmp_path)])
    assert exit_code == 11


def test_cli_forced_provider_unknown_kind_exits_2(tmp_path: Path) -> None:
    """`--provider bogus` is an unregistered kind → exit 2 (INVALID_ARGS)."""
    exit_code = main(["--provider", "bogus", "/tmp/x.pdf", "--out", str(tmp_path)])
    assert exit_code == 2


# ── list-providers + probe ──────────────────────────────────────────


def test_cli_list_providers_includes_all_v1_providers(capsys) -> None:
    exit_code = main(["--list-providers"])
    out = capsys.readouterr().out
    assert exit_code == 0
    kinds = out.strip().splitlines()
    assert kinds == ["youtube", "pdf", "docs", "text", "image", "html"]


@pytest.mark.parametrize("input_str,expected_kind", [
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "youtube"),
    ("https://arxiv.org/pdf/2401.12345.pdf", "pdf"),
    ("/tmp/foo.pdf", "pdf"),
    ("/tmp/foo.docx", "docs"),
    ("/tmp/foo.xlsx", "docs"),
    ("/tmp/foo.pptx", "docs"),
    ("/tmp/foo.epub", "docs"),
    ("https://example.com/sheet.xlsx", "docs"),
    ("https://example.com/article", "html"),
    ("https://x.com/user/status/123", "html"),
])
def test_cli_probe_dispatches_correctly(input_str, expected_kind, capsys) -> None:
    exit_code = main(["--probe", input_str])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert f"Provider: {expected_kind}" in out
