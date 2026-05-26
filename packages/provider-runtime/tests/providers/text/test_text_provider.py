from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.providers.text.text import TextProvider


def test_matches_local_md_and_txt(tmp_path):
    prov = TextProvider()
    assert prov.matches("/notes/readme.md") is not None
    assert prov.matches("/notes/log.txt") is not None
    assert prov.matches("/notes/a.markdown") is not None
    assert prov.matches("https://example.com/x.md") is None  # remote → html provider
    assert prov.matches("/notes/a.pdf") is None


def test_extract_passthrough_preserves_markdown(tmp_path):
    src = tmp_path / "note.md"
    src.write_text("# Heading\n\n- one\n- two\n", encoding="utf-8")
    prov = TextProvider()
    det = prov.matches(str(src))
    res = prov.extract(det, ExtractionContext(out_dir=tmp_path, work_dir=tmp_path))
    assert res.status == "success"
    assert res.kind == "text"
    assert res.text == "# Heading\n\n- one\n- two\n"
    assert res.metadata.title == "Heading"   # first heading becomes the title
    assert res.extractor_detail["structured"] is True


def test_extract_missing_file_fails(tmp_path):
    prov = TextProvider()
    det = prov.matches(str(tmp_path / "nope.txt"))
    res = prov.extract(det, ExtractionContext(out_dir=tmp_path, work_dir=tmp_path))
    assert res.status == "failed"
    assert res.exit_code is not None
