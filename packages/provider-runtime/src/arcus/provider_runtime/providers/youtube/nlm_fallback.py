"""NLM CLI subprocess wrapper + transcript parsing.

Subprocess wrappers target the `nlm` CLI as of 2026-05-18:
  - `nlm login --check`           (auth check, not `nlm auth status`)
  - `nlm notebook create [TITLE]` (title is positional)
  - `nlm source add <nb_id> --url <url> --wait` (singular `source`;
                                                 `--wait` blocks until ingest)
  - `nlm source content <src_id>` (takes only source_id, not nb_id)
  - `nlm notebook delete <nb_id>` (unchanged)

The plan originally specified `nlm auth`, `nlm sources` (plural), and a
separate `wait_for_ingest()` polling loop. nlm now has `--wait` built in,
so polling is no longer required. See plan A.0 Task 10 spec-gap note.
"""

from __future__ import annotations

import re
import subprocess
from typing import Final

from arcus.provider_runtime.types import Segment


class NlmError(Exception):
    """Raised when an nlm subcommand exits non-zero."""


class NlmNotAuthenticatedError(Exception):
    """Raised when nlm reports unauthenticated state."""


_DEFAULT_INGEST_TIMEOUT_SECONDS: Final[int] = 300


def _run_nlm(args: list[str], timeout: int | None = None) -> tuple[str, str, int]:
    """Run `nlm <args...>` with argv list (no shell). Returns (stdout, stderr, returncode)."""
    proc = subprocess.run(
        ["nlm", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.stdout, proc.stderr, proc.returncode


def check_auth() -> None:
    """Raise NlmNotAuthenticatedError if `nlm login --check` returns non-zero."""
    _, _, code = _run_nlm(["login", "--check"])
    if code != 0:
        raise NlmNotAuthenticatedError("nlm is not authenticated. Run `nlm login`.")


def create_notebook(name: str) -> str:
    """Create a NotebookLM notebook; return its ID.

    `nlm notebook create [TITLE]` returns the notebook ID on stdout (last
    whitespace-separated token). If the output format changes to JSON,
    update this parser.
    """
    stdout, stderr, code = _run_nlm(["notebook", "create", name])
    if code != 0:
        raise NlmError(f"nlm notebook create failed: {stderr.strip()}")
    nb_id = stdout.strip().split()[-1] if stdout.strip() else ""
    if not nb_id:
        raise NlmError(f"nlm notebook create returned no ID: {stdout!r}")
    return nb_id


def add_url_source(
    notebook_id: str,
    url: str,
    timeout_seconds: int = _DEFAULT_INGEST_TIMEOUT_SECONDS,
) -> str:
    """Add a URL source to a notebook and wait for ingest; return the source ID.

    Uses `nlm source add <nb_id> --url <url> --wait` so this call blocks
    until the source finishes processing. No separate polling needed.
    """
    stdout, stderr, code = _run_nlm(
        ["source", "add", notebook_id, "--url", url, "--wait"],
        timeout=timeout_seconds,
    )
    if code != 0:
        raise NlmError(f"nlm source add failed: {stderr.strip()}")
    src_id = stdout.strip().split()[-1] if stdout.strip() else ""
    if not src_id:
        raise NlmError(f"nlm source add returned no ID: {stdout!r}")
    return src_id


def get_source_content(source_id: str) -> str:
    """Fetch transcript text from an NLM source.

    `nlm source content <src_id>` takes only the source ID (no notebook ID).
    """
    stdout, stderr, code = _run_nlm(["source", "content", source_id])
    if code != 0:
        raise NlmError(f"nlm source content failed: {stderr.strip()}")
    return stdout


def delete_notebook(notebook_id: str) -> None:
    """Delete a NotebookLM notebook (cleanup)."""
    _, stderr, code = _run_nlm(["notebook", "delete", notebook_id])
    if code != 0:
        raise NlmError(f"nlm notebook delete failed: {stderr.strip()}")


def parse_transcript_to_segments(text: str, duration_ms: int) -> list[Segment]:
    """Convert NLM's plain-text transcript into Segment list, distributing time evenly."""
    trimmed = text.strip()
    if not trimmed:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", trimmed) if p.strip()]
    if not paragraphs:
        return []
    if len(paragraphs) == 1:
        return [Segment(start_ms=0, end_ms=duration_ms, text=paragraphs[0])]

    slice_ms = duration_ms // len(paragraphs)
    out: list[Segment] = []
    for i, p in enumerate(paragraphs):
        end = duration_ms if i == len(paragraphs) - 1 else (i + 1) * slice_ms
        out.append(Segment(start_ms=i * slice_ms, end_ms=end, text=p))
    return out
