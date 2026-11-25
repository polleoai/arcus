"""#24: text from any captured source must stay text in the note (policy, PGM 2026-11-25).

Every captured source is untrusted. Non-markdown sources (txt, pdf, docs,
youtube transcripts, image OCR) become escaped text; a source that is itself
markdown (.md) keeps its markdown, sanitised (raw HTML escaped, links and
embeds http(s) only, executable fences and inline queries made inert); and
Obsidian wikilinks / embeds are escaped everywhere. The writer's title
heading and figure alt text are escaped too. Checked on result.text (what
library callers get) and segment text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers._shared import markdown_text as mt
from arcus.provider_runtime.types import ExtractionResult, SourceMetadata

BT = "`"
EVIL = "http://127.0.0.1:9/px"
PAYLOAD = [
    f'see <img src="{EVIL}"> and ![x]({EVIL}) here',
    "link [l](file:///etc/passwd) and [[note]] and ![[secret.png]]",
    f"query {BT}$= dv.current(){BT} and {BT}= this.file.name{BT}",
    "templater <% tp.file.title %> and <%* tR += 1 %>",
    "~~~dataviewjs", "dv.paragraph(1)", "~~~",
    f"{BT * 3}dataviewjs", "dv.paragraph(2)", BT * 3,
    "[r]: file:///etc/passwd",
]
EXEC_LANGS = ("dataviewjs", "dataview", "js-engine", "query", "tasks", "button", "run-js")
_PREFIX = r"^(?:[ \t>]|[-*+][ \t]|\d{1,9}[.)][ \t])*"
FENCE_LINE = re.compile(_PREFIX + r"(?:" + BT + r"{3,}|~{3,})(.*)$", re.M)
UNESCAPED = r"(?<!\\)(?:\\\\)*"
TARGET = re.compile(UNESCAPED + r"\]\(\s*([^)\s]*)")
REFDEF = re.compile(UNESCAPED + r"\]:\s*(\S*)")
INLINE_QUERY = re.compile(UNESCAPED + BT + r"+[ \t]*\$?=")
LIVE_TAG = re.compile(r"<\s*/?\s*[a-zA-Z!%]")


def _outside_code_blocks(md: str) -> str:
    """md with the content of column-0 fenced blocks removed (code renders as text)."""
    out, fence = [], None
    for line in md.split("\n"):
        if fence is None:
            m = re.match(r"(`{3,}|~{3,})", line)
            if m:
                fence = m.group(1)
            out.append(line)
        elif re.match(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*$", line):
            fence = None
            out.append(line)
    return "\n".join(out)


def assert_safe(md: str, *, fences_allowed: bool = False, local_ok: str = "") -> None:
    assert "<%" not in md, md  # Templater reads raw text, code blocks included
    if fences_allowed:
        md = _outside_code_blocks(md)
    assert not LIVE_TAG.search(md), md
    assert "<%" not in md, md
    assert "[[" not in md, md
    for m in TARGET.finditer(md):
        t = m.group(1)
        assert re.match(r"https?://", t) or (local_ok and t.startswith(local_ok)), (t, md)
    for m in REFDEF.finditer(md):
        assert re.match(r"https?://", m.group(1)), (m.group(1), md)
    assert not INLINE_QUERY.search(md), md
    for m in FENCE_LINE.finditer(md):
        assert fences_allowed, ("fence in escaped text", m.group(0), md)
        info = m.group(1).strip().split()[:1]
        assert not info or info[0].lower() not in EXEC_LANGS, (m.group(0), md)


def _ctx(tmp_path):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return ExtractionContext(out_dir=tmp_path, work_dir=work)


# ── shared helpers ───────────────────────────────────────────────────────────


def test_to_markdown_text_escapes_and_keeps_figure_tokens():
    from arcus.provider_runtime.providers._shared.figures import figure_token
    tok = figure_token(0)
    out = mt.to_markdown_text("\n".join(PAYLOAD[:4] + [tok] + PAYLOAD[4:]))
    assert_safe(out.replace(tok, ""))
    assert tok in out.splitlines()


@pytest.mark.parametrize("tool,raw,shows", [
    ("pandoc", "# Title \\<b\\>x\\</b\\> \\[\\[t\\]\\] \\" + BT + "\\$= x\\" + BT + " \\<% tp %\\>",
     "Title &lt;b>x&lt;/b> \\[\\[t\\]\\]"),
    ("docling", "&lt;img src=\"" + EVIL + "\"&gt; snake\\_case [[note]] " + BT + "$= x" + BT, "snake_case"),
    ("pymupdf4llm", "|a<br>b|c|\n|---|---|", "|a b|c|"),
])
def test_tool_output_is_decoded_once_then_escaped(tool, raw, shows):
    out = mt.tool_output_to_markdown(raw, tool)
    assert_safe(out)
    assert shows in out, out
    assert "&amp;lt;" not in out and "\\\\" not in out, out  # no double escaping


# ── markdown sources (.md) ───────────────────────────────────────────────────


def test_sanitize_markdown_neutralises_every_vector():
    out = mt.sanitize_markdown("\n".join(PAYLOAD + ["\\<% tp.file.title %>", "- " + BT * 3 + "dataviewjs", "  ~~~js-engine"]))
    assert_safe(out, fences_allowed=True)


def test_sanitize_markdown_keeps_ordinary_markdown():
    md = "\n".join([
        "# Heading", "", "Some **bold** text with [a link](https://ok.test/a) and ![img](https://ok.test/i.png).",
        "", "- item one", "- item two", "", "| a | b |", "|---|---|", "| 1 | 2 |", "",
        BT * 3 + "python", "x = '<div>'  # literal", BT * 3, "",
        "[ref]: https://ok.test/r",
    ])
    out = mt.sanitize_markdown(md)
    assert_safe(out, fences_allowed=True)
    for keep in ("# Heading", "**bold**", "[a link](https://ok.test/a)", "![img](https://ok.test/i.png)",
                 "- item one", "| a | b |", BT * 3 + "python\nx = '<div>'  # literal\n" + BT * 3,
                 "[ref]: https://ok.test/r"):
        assert keep in out, (keep, out)


def test_sanitize_markdown_strips_executable_fence_info():
    out = mt.sanitize_markdown(BT * 3 + "dataviewjs\ndv.paragraph(1)\n" + BT * 3 + "\n~~~query\ntag:#x\n~~~")
    assert_safe(out, fences_allowed=True)
    assert "dv.paragraph(1)" in out


def test_sanitize_markdown_templater_inside_a_fence_is_inert():
    out = mt.sanitize_markdown(BT * 3 + "erb\n<%= @user.name %>\n" + BT * 3)
    assert "<%" not in out and "&lt;%= @user.name %>" in out, out


def test_sanitize_markdown_turns_frontmatter_into_a_yaml_block():
    out = mt.sanitize_markdown("---\ntitle: <img src=x>\ntags: [a]\n---\n\nBody\n")
    assert_safe(out, fences_allowed=True)
    assert out.startswith(BT * 3 + "yaml\n"), out


# ── providers ────────────────────────────────────────────────────────────────


def _text_result(tmp_path, name, body):
    from arcus.provider_runtime.providers.text.text import TextProvider
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    prov = TextProvider()
    res = prov.extract(prov.matches(str(p)), _ctx(tmp_path))
    assert res.status == "success", res.error
    return res


def test_text_provider_txt_is_escaped_text(tmp_path):
    res = _text_result(tmp_path, "evil.txt", "\n".join(PAYLOAD))
    assert_safe(res.text)
    assert "dv.paragraph(1)" in res.text


def test_text_provider_md_is_sanitised_markdown(tmp_path):
    res = _text_result(tmp_path, "evil.md", "# Notes\n\n[ok](https://ok.test/)\n\n" + "\n".join(PAYLOAD))
    assert_safe(res.text, fences_allowed=True)
    assert "[ok](https://ok.test/)" in res.text and "# Notes" in res.text


def _no_docling(monkeypatch, use_docling):
    # Docling is the primary tier when installed; without it the lighter
    # tiers (pymupdf4llm / pdftotext, python-docx / pandoc) run. Cover both.
    if not use_docling:
        from arcus.provider_runtime.providers._shared import docling_extract
        monkeypatch.setattr(docling_extract, "convert", lambda *a, **k: None)


@pytest.mark.parametrize("use_docling", [True, False], ids=["docling", "fallback-tiers"])
def test_pdf_provider_text_and_segments_are_escaped(tmp_path, monkeypatch, use_docling):
    _no_docling(monkeypatch, use_docling)
    pymupdf = pytest.importorskip("pymupdf")
    from arcus.provider_runtime.providers.pdf.pdf import PdfProvider
    path = tmp_path / "evil.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in PAYLOAD:
        page.insert_text((50, y), line, fontsize=9)
        y += 14
    doc.save(path)
    prov = PdfProvider()
    res = prov.extract(prov.matches(str(path)), _ctx(tmp_path))
    assert res.status == "success", res.error
    assert_safe(res.text, local_ok="evil.figures/")
    for s in res.segments:
        assert_safe(s.text)


@pytest.mark.parametrize("use_docling", [True, False], ids=["docling", "fallback-tiers"])
def test_docs_provider_text_is_escaped(tmp_path, monkeypatch, use_docling):
    _no_docling(monkeypatch, use_docling)
    docx = pytest.importorskip("docx")
    from arcus.provider_runtime.providers.docs.docs import DocsProvider
    path = tmp_path / "evil.docx"
    d = docx.Document()
    d.add_heading("Title <b>x</b> [[t]]", 1)
    for line in PAYLOAD:
        d.add_paragraph(line)
    d.save(path)
    prov = DocsProvider()
    res = prov.extract(prov.matches(str(path)), _ctx(tmp_path))
    assert res.status == "success", res.error
    assert_safe(res.text)
    for s in res.segments:
        assert_safe(s.text)


def test_docs_provider_pandoc_tier_is_escaped(tmp_path, monkeypatch):
    import shutil
    import subprocess
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc not installed")
    _no_docling(monkeypatch, False)
    from arcus.provider_runtime.providers.docs.docs import DocsProvider
    src = tmp_path / "src.txt"
    src.write_text("\n\n".join(PAYLOAD), encoding="utf-8")
    path = tmp_path / "evil.epub"
    subprocess.run(["pandoc", "--from", "markdown", "--metadata", "title=t", "-o", str(path), str(src)], check=True)
    prov = DocsProvider()
    res = prov.extract(prov.matches(str(path)), _ctx(tmp_path))
    assert res.status == "success", res.error
    assert res.extractor_detail.get("extractor") == "pandoc", res.extractor_detail
    assert_safe(res.text)
    assert "\\\\" not in res.text, res.text  # pandoc's escapes decoded, not doubled


def test_docling_result_is_decoded_once_and_escaped():
    from arcus.provider_runtime.providers._shared import docling_extract
    md = ("# Report\n\n&lt;img src=\"" + EVIL + "\"&gt; and snake\\_case\n\n" + "\n".join(PAYLOAD[1:]))
    res = docling_extract.to_extraction_result(
        "pdf", "/x.pdf", "x", docling_extract.DoclingResult(markdown=md, segments=[], locators=[]))
    assert_safe(res.text)
    assert "&amp;lt;" not in res.text and "snake_case" in res.text, res.text


def test_youtube_captions_are_escaped(mocker, tmp_path):
    from arcus.provider_runtime.providers.youtube import youtube as yt
    from arcus.provider_runtime.providers.youtube.ytdlp_adapter import (
        FetchCaptionsResult, SubtitleTrack, YtDlpMetadata)
    yt._METADATA_CACHE.clear()
    cues = "".join(f"00:00:{i:02d}.000 --> 00:00:{i + 1:02d}.000\n{line}\n\n" for i, line in enumerate(PAYLOAD))
    mocker.patch.object(yt, "fetch_metadata", return_value=YtDlpMetadata(
        title="<img src=x> t", channel="c", duration_ms=60_000, posted=None, language=None,
        subtitle_tracks=[SubtitleTrack(lang="en", source="uploader")]))
    mocker.patch.object(yt, "fetch_captions", return_value=FetchCaptionsResult(
        vtt_content="WEBVTT\n\n" + cues, selected_track=SubtitleTrack(lang="en", source="uploader")))
    prov = yt.YouTubeProvider()
    res = prov.extract(prov.matches("https://www.youtube.com/watch?v=abcdefghijk"), _ctx(tmp_path))
    yt._METADATA_CACHE.clear()
    assert res.status == "success", res.error
    assert_safe(res.text)
    for s in res.segments:
        assert_safe(s.text)


def test_youtube_nlm_transcript_is_escaped(mocker, tmp_path):
    from arcus.provider_runtime.providers.youtube import youtube as yt
    from arcus.provider_runtime.providers.youtube.ytdlp_adapter import YtDlpMetadata
    yt._METADATA_CACHE.clear()
    mocker.patch.object(yt, "fetch_metadata", return_value=YtDlpMetadata(
        title="t", channel="c", duration_ms=60_000, posted=None, language=None, subtitle_tracks=[]))
    for name in ("check_auth", "delete_notebook"):
        mocker.patch.object(yt, name, return_value=None)
    mocker.patch.object(yt, "load_cached_limit", return_value=100)
    mocker.patch.object(yt, "create_notebook", return_value="nb")
    mocker.patch.object(yt, "add_url_source", return_value="src")
    mocker.patch.object(yt, "get_source_content", return_value="\n\n".join(PAYLOAD))
    mocker.patch.object(yt.url_guard, "check_url", return_value=None)
    prov = yt.YouTubeProvider()
    res = prov.extract(prov.matches("https://www.youtube.com/watch?v=abcdefghijk"), _ctx(tmp_path))
    yt._METADATA_CACHE.clear()
    assert res.status == "success", res.error
    assert_safe(res.text)
    for s in res.segments:
        assert_safe(s.text)


def test_image_ocr_text_is_escaped(tmp_path):
    from unittest.mock import patch
    from arcus.provider_runtime.providers.image import image as image_mod
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    prov = image_mod.ImageProvider()
    with patch.object(image_mod, "_recognize", return_value=("\n".join(PAYLOAD), False, "rapidocr")):
        res = prov.extract(prov.matches(str(img)), _ctx(tmp_path))
    assert res.status == "success", res.error
    assert_safe(res.text)


def test_figure_alt_text_is_escaped():
    from arcus.provider_runtime.providers._shared import figures
    PIL = pytest.importorskip("PIL.Image")
    import io
    buf = io.BytesIO()
    PIL.new("RGB", (480, 320), (30, 90, 200)).save(buf, "PNG")
    opts = figures.FigureOptions()
    raw = figures.RawFigure(data=buf.getvalue(), page=1, width_pt=300, height_pt=200,
                            caption=f'Figure 1: <img src="{EVIL}"> [[note]] ](file:///x)')
    md, assets, kept, _ = figures.place_figures("before\n\n" + figures.figure_token(0) + "\n\nafter", [raw], "s", opts)
    assert assets, "figure not placed"
    assert_safe(md, local_ok="s.figures/")
    assert "s.figures/figure-01-p1.png" in md


# ── writer ───────────────────────────────────────────────────────────────────


def test_writer_title_heading_is_escaped(tmp_path):
    import yaml
    from arcus.provider_runtime import writer
    title = f'<img src="{EVIL}"> T [[x]] ' + BT + "$= dv.current()" + BT
    res = ExtractionResult(status="success", kind="pdf", extractor_detail={},
                           metadata=SourceMetadata(source="/x.pdf", source_id="/x.pdf", title=title, slug="w"),
                           text="plain body", segments=[], extracted_at="2026-11-25T00:00:00Z")
    md_path, _ = writer.write_success(tmp_path, "w", res)
    md = md_path.read_text(encoding="utf-8")
    fm, body = md.split("\n---\n", 1)
    assert yaml.safe_load(fm.lstrip("-\n"))["title"] == title  # metadata stays the plain title
    assert_safe(body)
