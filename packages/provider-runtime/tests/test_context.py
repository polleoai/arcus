from arcus.provider_runtime.provider_interface import ExtractionContext


def test_emit_progress_defaults_to_noop(tmp_path):
    """A context built without a reporter must not raise when a provider
    emits progress — keeps providers usable in unit tests."""
    ctx = ExtractionContext(out_dir=tmp_path, work_dir=tmp_path)
    ctx.emit_progress("fetching")  # must be a no-op, no error


def test_emit_progress_invokes_reporter(tmp_path):
    seen = []
    ctx = ExtractionContext(
        out_dir=tmp_path,
        work_dir=tmp_path,
        emit_progress=seen.append,
    )
    ctx.emit_progress("extracting")
    assert seen == ["extracting"]
