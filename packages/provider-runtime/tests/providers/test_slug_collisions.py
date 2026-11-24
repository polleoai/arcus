"""Two inputs that map to one slug never overwrite each other (#14).

Every provider that derives its slug from a file or URL stem (pdf, docs, text,
image, html) used to write `<slug>.md`/`.json`/`.figures/` for whichever
source ran last. Each source now owns its output name: the first keeps the bare
slug, later ones get `<slug>--<hash>`, and re-running a source finds its own
output again. Assertions check the files written to out_dir.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from arcus.provider_runtime import Factory, register_defaults
from arcus.provider_runtime.registry import ProviderRegistry
from arcus.provider_runtime.writer import cache_hit_exists


def _factory() -> Factory:
    reg = ProviderRegistry()
    register_defaults(reg)
    return Factory(registry=reg)


def _outputs(out: Path) -> dict[str, dict]:
    """source_id -> {md, json, figures} for every success/failed output."""
    found = {}
    for md in sorted(out.glob("*.md")):
        payload = json.loads(md.with_suffix(".json").read_text())
        sid = payload.get("source_id") or payload["metadata"]["source_id"]
        assert sid not in found, f"two outputs for {sid}"
        fig = md.parent / f"{md.stem}.figures"
        found[sid] = {
            "md": md,
            "status": payload["status"],
            "figures": sorted(p.name for p in fig.glob("*.png")) if fig.is_dir() else [],
            "text": md.read_text(),
        }
    return found


def _snapshot(out: Path) -> dict[str, bytes]:
    return {str(p.relative_to(out)): p.read_bytes()
            for p in sorted(out.rglob("*")) if p.is_file() and ".log" not in p.parts}


# ── pdf: md, json and figures ───────────────────────────────────────────────

pdf_deps = pytest.mark.skipif(
    not all(__import__("importlib").util.find_spec(m) for m in ("pymupdf4llm", "PIL")),
    reason="needs the [pdf] extra and Pillow",
)


def _mk(path: Path, n_figures: int) -> Path:
    from .pdf._figure_pdf import make_figure_pdf

    path.parent.mkdir(parents=True, exist_ok=True)
    return make_figure_pdf(path, n_figures=n_figures)


@pdf_deps
def test_two_local_pdfs_with_one_stem_keep_their_own_outputs(tmp_path):

    a = _mk(tmp_path / "a" / "paper.pdf", n_figures=2)
    b = _mk(tmp_path / "b" / "paper.pdf", n_figures=3)
    out = tmp_path / "out"
    f = _factory()

    assert f.run(str(a), out_dir=out) == 0
    first = _snapshot(out)
    assert f.run(str(b), out_dir=out) == 0

    got = _outputs(out)
    assert set(got) == {str(a), str(b)}
    assert len(got[str(a)]["figures"]) == 2
    assert len(got[str(b)]["figures"]) == 3
    assert got[str(a)]["md"] != got[str(b)]["md"]
    # The first source's files are byte-identical after the second run.
    after = _snapshot(out)
    for name, data in first.items():
        assert after.get(name) == data, f"{name} changed"
    # Each body references only its own figures folder.
    for sid, o in got.items():
        stem = o["md"].stem
        targets = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", o["text"])
        assert targets and all(t.startswith(f"{stem}.figures/") for t in targets), (sid, targets)


@pdf_deps
def test_each_source_cache_hits_its_own_output(tmp_path):

    a = _mk(tmp_path / "a" / "paper.pdf", n_figures=2)
    b = _mk(tmp_path / "b" / "paper.pdf", n_figures=3)
    out = tmp_path / "out"
    f = _factory()
    f.run(str(a), out_dir=out)
    f.run(str(b), out_dir=out)
    before = _snapshot(out)
    assert f.run(str(a), out_dir=out) == 0
    assert f.run(str(b), out_dir=out) == 0
    assert _snapshot(out) == before
    hits = [json.loads(ln) for ln in (out / ".log" / "extract-log.ndjson").read_text().splitlines()]
    hits = [e for e in hits if e["event"] == "cache_hit"]
    assert sorted(e["source_id"] for e in hits) == sorted([str(a), str(b)])
    for sid in (str(a), str(b)):
        assert cache_hit_exists(out, "paper", sid) is not None


@pdf_deps
def test_forced_reextraction_reuses_the_sources_own_name(tmp_path):

    a = _mk(tmp_path / "a" / "paper.pdf", n_figures=2)
    b = _mk(tmp_path / "b" / "paper.pdf", n_figures=3)
    out = tmp_path / "out"
    f = _factory()
    f.run(str(a), out_dir=out)
    f.run(str(b), out_dir=out)
    names = sorted(p.name for p in out.glob("*.md"))
    assert f.run(str(b), out_dir=out, force=True) == 0
    assert f.run(str(a), out_dir=out, force=True) == 0
    assert sorted(p.name for p in out.glob("*.md")) == names
    got = _outputs(out)
    assert [len(got[str(a)]["figures"]), len(got[str(b)]["figures"])] == [2, 3]


@pdf_deps
def test_failed_extraction_of_another_source_keeps_earlier_figures(tmp_path):

    a = _mk(tmp_path / "a" / "paper.pdf", n_figures=2)
    bad = tmp_path / "b" / "paper.pdf"
    bad.parent.mkdir()
    bad.write_bytes(b"%PDF-1.4 not really a pdf")
    out = tmp_path / "out"
    f = _factory()
    assert f.run(str(a), out_dir=out) == 0
    first = _snapshot(out)
    assert f.run(str(bad), out_dir=out) != 0

    after = _snapshot(out)
    for name, data in first.items():
        assert after.get(name) == data, f"{name} changed"
    got = _outputs(out)
    assert got[str(a)]["status"] == "success" and len(got[str(a)]["figures"]) == 2
    assert got[str(bad)]["status"] == "failed"


@pdf_deps
def test_local_pdf_and_remote_pdf_with_one_stem(tmp_path, monkeypatch):
    import shutil

    from arcus.provider_runtime import url_guard
    from arcus.provider_runtime.providers.pdf.pdf import PdfProvider

    a = _mk(tmp_path / "a" / "paper.pdf", n_figures=2)
    remote_src = _mk(tmp_path / "r" / "paper.pdf", n_figures=3)
    url = "https://example.org/files/paper.pdf"
    monkeypatch.setattr(PdfProvider, "_head_content_type", lambda self, u: "application/pdf")
    monkeypatch.setattr(url_guard, "download", lambda u, dest: shutil.copy(remote_src, dest))
    out = tmp_path / "out"
    f = _factory()
    assert f.run(str(a), out_dir=out) == 0
    first = _snapshot(out)
    assert f.run(url, out_dir=out) == 0
    after = _snapshot(out)
    for name, data in first.items():
        assert after.get(name) == data, f"{name} changed"
    got = _outputs(out)
    assert set(got) == {str(a), url}
    assert [len(got[str(a)]["figures"]), len(got[url]["figures"])] == [2, 3]


# ── the other stem-derived providers ────────────────────────────────────────

def test_two_text_files_with_one_stem(tmp_path):
    a = tmp_path / "a" / "notes.md"
    b = tmp_path / "b" / "notes.md"
    for p, body in ((a, "alpha body"), (b, "beta body")):
        p.parent.mkdir()
        p.write_text(f"# Notes\n\n{body}\n")
    out = tmp_path / "out"
    f = _factory()
    assert f.run(str(a), out_dir=out) == 0
    assert f.run(str(b), out_dir=out) == 0
    got = _outputs(out)
    assert set(got) == {str(a), str(b)}
    assert "alpha body" in got[str(a)]["text"] and "beta body" in got[str(b)]["text"]


def test_two_docs_with_one_stem(tmp_path):
    a = tmp_path / "a" / "report.docx"
    b = tmp_path / "b" / "report.docx"
    for p in (a, b):
        p.parent.mkdir()
        p.write_bytes(b"fake docx")
    out = tmp_path / "out"
    f = _factory()
    fake = lambda fp, ext: {"title": "Report", "authors": "", "text": f"body from {Path(fp).parent.name}"}
    with patch("arcus.provider_runtime.providers._shared.file_extract.extract_text", side_effect=fake):
        assert f.run(str(a), out_dir=out) == 0
        assert f.run(str(b), out_dir=out) == 0
    got = _outputs(out)
    assert set(got) == {str(a), str(b)}
    assert "body from a" in got[str(a)]["text"] and "body from b" in got[str(b)]["text"]


def test_two_html_urls_with_one_slug(tmp_path, monkeypatch):
    from arcus.provider_runtime.providers.html import _athena_fetch_page

    u1 = "https://example.com/article?id=1"
    u2 = "https://example.com/article?id=2"
    monkeypatch.setattr(_athena_fetch_page, "fetch_page",
                        lambda url: f"# Article\n\nThe text of {url[-4:]} is here.\n")
    out = tmp_path / "out"
    f = _factory()
    assert f.run(u1, out_dir=out) == 0
    assert f.run(u2, out_dir=out) == 0
    got = _outputs(out)
    assert set(got) == {u1, u2}
    assert "id=1" in got[u1]["text"] and "id=2" in got[u2]["text"]


# ── the writer itself refuses to overwrite another source ───────────────────

def _result(source_id: str, slug: str):
    from arcus.provider_runtime.types import ExtractionResult, SourceMetadata

    return ExtractionResult(
        status="success", kind="text", extractor_detail={},
        metadata=SourceMetadata(source=source_id, source_id=source_id, title="T", slug=slug),
        text=f"body of {source_id}", segments=[], extracted_at="2026-11-23T00:00:00+00:00",
    )


def test_writer_refuses_a_slug_owned_by_another_source(tmp_path):
    from arcus.provider_runtime.writer import SlugTakenError, write_failure_stub, write_success

    write_success(tmp_path, "same", _result("src-a", "same"))
    before = _snapshot(tmp_path)
    with pytest.raises(SlugTakenError):
        write_success(tmp_path, "same", _result("src-b", "same"))
    with pytest.raises(SlugTakenError):
        write_failure_stub(tmp_path, slug="same", source="src-b", source_id="src-b",
                           kind="text", title=None, exit_code=10,
                           extractor_attempted=["text"], error="boom")
    assert _snapshot(tmp_path) == before
    # The owner itself may rewrite its own output.
    write_success(tmp_path, "same", _result("src-a", "same"))


def test_factory_reports_a_provider_that_ignores_its_claimed_slug(tmp_path):
    """A provider that writes a fixed slug for every source must fail loudly
    for the second source, never overwrite the first."""
    from arcus.provider_runtime.types import EXIT_CODES

    class FixedSlug:
        kind = "fixed"

        def matches(self, raw):
            return __import__("arcus.provider_runtime.types", fromlist=["DetectionResult"]).DetectionResult(
                kind="fixed", source_id=raw, raw=raw, metadata={}) if raw.startswith("fixed:") else None

        def predict_slug(self, d):
            return "same"

        def extract(self, d, ctx):
            return _result(d.source_id, "same")

    reg = ProviderRegistry()
    reg.register(FixedSlug())
    f = Factory(registry=reg)
    assert f.run("fixed:a", out_dir=tmp_path) == 0
    before = _snapshot(tmp_path)
    assert f.run("fixed:b", out_dir=tmp_path) == EXIT_CODES["PROVIDER_PRIMARY_FAILED"]
    assert _snapshot(tmp_path) == before
    events = [json.loads(ln) for ln in (tmp_path / ".log" / "extract-log.ndjson").read_text().splitlines()]
    assert events[-1]["event"] == "failed" and "same" in events[-1]["error"]


def test_two_images_with_one_stem(tmp_path):
    from arcus.provider_runtime.providers.image import image as image_mod

    a = tmp_path / "a" / "scan.png"
    b = tmp_path / "b" / "scan.png"
    for p in (a, b):
        p.parent.mkdir()
        p.write_bytes(b"\x89PNG fake")
    out = tmp_path / "out"
    f = _factory()
    with patch.object(image_mod, "_recognize",
                      side_effect=lambda fp: (f"text from {Path(fp).parent.name}", False, "rapidocr")):
        assert f.run(str(a), out_dir=out) == 0
        assert f.run(str(b), out_dir=out) == 0
    got = _outputs(out)
    assert set(got) == {str(a), str(b)}
    assert "text from a" in got[str(a)]["text"] and "text from b" in got[str(b)]["text"]


def test_youtube_failure_with_a_shared_title_keeps_the_other_video(tmp_path, mocker):
    """Two videos titled alike: B's failure stub must not replace A's
    transcript, and a forced re-run of A reuses A's own file."""
    from arcus.provider_runtime.providers.youtube import youtube as yt
    from arcus.provider_runtime.providers.youtube.nlm_fallback import NlmNotAuthenticatedError
    from arcus.provider_runtime.providers.youtube.ytdlp_adapter import (
        FetchCaptionsResult, SubtitleTrack, YtDlpMetadata)

    yt._METADATA_CACHE.clear()
    track = SubtitleTrack(lang="en", source="uploader")
    ua = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    ub = "https://www.youtube.com/watch?v=bbbbbbbbbbb"

    def meta(url):
        return YtDlpMetadata(title="Sample", channel="Ch", duration_ms=1000, posted=None,
                             language=None, subtitle_tracks=[track] if url == ua else [])

    def no_auth():
        raise NlmNotAuthenticatedError("not logged in")

    mocker.patch.object(yt, "fetch_metadata", side_effect=meta)
    mocker.patch.object(yt, "fetch_captions", return_value=FetchCaptionsResult(
        vtt_content="WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello there.\n",
        selected_track=track))
    mocker.patch.object(yt, "check_auth", side_effect=no_auth)
    out = tmp_path / "out"
    f = _factory()
    try:
        assert f.run(ua, out_dir=out) == 0
        first = _snapshot(out)
        assert f.run(ub, out_dir=out) != 0
        after = _snapshot(out)
        for name, data in first.items():
            assert after.get(name) == data, f"{name} changed"
        got = _outputs(out)
        assert got["aaaaaaaaaaa"]["status"] == "success"
        assert got["bbbbbbbbbbb"]["status"] == "failed"
        names = sorted(p.name for p in out.glob("*.md"))
        assert f.run(ua, out_dir=out, force=True) == 0
        assert sorted(p.name for p in out.glob("*.md")) == names
    finally:
        yt._METADATA_CACHE.clear()
