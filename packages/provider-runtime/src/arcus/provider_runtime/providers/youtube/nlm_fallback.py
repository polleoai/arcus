"""NLM CLI subprocess wrapper + transcript parsing.

Runtime dependency: the `nlm` binary from **notebooklm-mcp-cli**
(https://github.com/jacob-bd/notebooklm-mcp-cli), declared as
`NLM_REQUIREMENT` below. It is a separate tool, not a Python import, so it
can't be a pyproject dependency; install it with
`uv tool install 'notebooklm-mcp-cli>=0.9.4,<1'` and authenticate with
`nlm login`. `check_auth()` verifies the installed version (`nlm --version`)
before the fallback runs and raises `NlmUnavailableError` with that
instruction when `nlm` is missing or out of range.

CLI shape, verified against notebooklm-mcp-cli 0.9.4 (the declared minimum):
  - `nlm --version`                 -> "nlm version X.Y.Z"
  - `nlm login --check`             exit 0 when authenticated
  - `nlm notebook create TITLE --json`          -> {"notebook_id": ...}
  - `nlm source add NB --url URL --wait --json` -> {"source_id": ...}
  - `nlm source content SRC --json`            -> {"content": ...}
  - `nlm notebook delete NB --confirm`  (without --confirm it prompts)
Piped (non-TTY) output is JSON whether or not `--json` is given, so it is
always requested and parsed; stdin is closed so no prompt can block.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Final

from arcus.provider_runtime.types import Segment


class NlmError(Exception):
    """Raised when an nlm subcommand exits non-zero."""


class NlmNotAuthenticatedError(Exception):
    """Raised when nlm reports unauthenticated state."""


class NlmUnavailableError(NlmError):
    """`nlm` is missing, or its version is outside NLM_REQUIREMENT."""


NLM_PACKAGE: Final[str] = "notebooklm-mcp-cli"
NLM_MIN_VERSION: Final[tuple[int, int, int]] = (0, 9, 4)   # verified CLI shape
NLM_MAX_VERSION: Final[tuple[int, int, int]] = (1, 0, 0)   # exclusive
NLM_REQUIREMENT: Final[str] = f"{NLM_PACKAGE}>=0.9.4,<1"
_INSTALL_HINT: Final[str] = (
    f"install it with `uv tool install '{NLM_REQUIREMENT}'`, then run `nlm login`")

_DEFAULT_INGEST_TIMEOUT_SECONDS: Final[int] = 300
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
# Version string of the nlm that passed the check (once per process).
_NLM_VERIFIED: str | None = None


def _run_nlm(args: list[str], timeout: int | None = None) -> tuple[str, str, int]:
    """Run `nlm <args...>` with argv list (no shell). Returns (stdout, stderr, returncode).

    stdin is closed so a confirmation prompt fails instead of blocking; a
    missing binary is NlmUnavailableError and a timeout NlmError."""
    try:
        proc = subprocess.run(
            ["nlm", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        raise NlmUnavailableError(
            f"the YouTube NotebookLM fallback needs {NLM_REQUIREMENT}, but `nlm` "
            f"was not found on PATH; {_INSTALL_HINT}") from e
    except subprocess.TimeoutExpired as e:
        raise NlmError(f"nlm {' '.join(args[:2])} timed out after {e.timeout}s") from e
    return proc.stdout, proc.stderr, proc.returncode


def require_nlm() -> str:
    """Verify the installed `nlm` satisfies NLM_REQUIREMENT; return its version.

    Raises NlmUnavailableError (actionable message) when it is missing, its
    version can't be read, or the version is out of range."""
    global _NLM_VERIFIED
    if _NLM_VERIFIED is not None:
        return _NLM_VERIFIED
    stdout, stderr, code = _run_nlm(["--version"], timeout=60)
    m = _VERSION_RE.search(stdout) if code == 0 else None
    if m is None:
        raise NlmUnavailableError(
            f"could not read the `nlm` version (`nlm --version` exit {code}: "
            f"{(stdout or stderr).strip()[:120]!r}); the YouTube NotebookLM fallback "
            f"needs {NLM_REQUIREMENT}; {_INSTALL_HINT}")
    found = tuple(int(x) for x in m.groups())
    version = m.group(0)
    if not (NLM_MIN_VERSION <= found < NLM_MAX_VERSION):
        raise NlmUnavailableError(
            f"`nlm` {version} is installed, but the YouTube NotebookLM fallback "
            f"needs {NLM_REQUIREMENT} (the CLI shape arcus drives); {_INSTALL_HINT}")
    _NLM_VERIFIED = version
    return version


def _json_out(what: str, stdout: str) -> dict[str, Any]:
    try:
        data = json.loads(stdout)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise NlmError(f"nlm {what} returned no JSON object (expected {NLM_REQUIREMENT}): "
                       f"{stdout.strip()[:200]!r}")
    return data


def _json_field(what: str, stdout: str, key: str) -> str:
    value = _json_out(what, stdout).get(key)
    if not isinstance(value, str) or not value:
        raise NlmError(f"nlm {what} returned no {key}: {stdout.strip()[:200]!r}")
    return value


def check_auth() -> None:
    """Verify the `nlm` version (require_nlm), then its login.

    Raises NlmUnavailableError or NlmNotAuthenticatedError."""
    require_nlm()
    _, _, code = _run_nlm(["login", "--check"], timeout=120)
    if code != 0:
        raise NlmNotAuthenticatedError("nlm is not authenticated. Run `nlm login`.")


def create_notebook(name: str) -> str:
    """Create a NotebookLM notebook; return its ID (`notebook_id` of the JSON)."""
    stdout, stderr, code = _run_nlm(["notebook", "create", name, "--json"], timeout=120)
    if code != 0:
        raise NlmError(f"nlm notebook create failed: {(stderr or stdout).strip()}")
    return _json_field("notebook create", stdout, "notebook_id")


def add_url_source(
    notebook_id: str,
    url: str,
    timeout_seconds: int = _DEFAULT_INGEST_TIMEOUT_SECONDS,
) -> str:
    """Add a URL source to a notebook and wait for ingest; return the source ID.

    Uses `nlm source add <nb_id> --url <url> --wait --json` so this call
    blocks until the source finishes processing; the ID is `source_id`.
    """
    stdout, stderr, code = _run_nlm(
        ["source", "add", notebook_id, "--url", url, "--wait",
         "--wait-timeout", str(timeout_seconds), "--json"],
        timeout=timeout_seconds + 60,
    )
    if code != 0:
        raise NlmError(f"nlm source add failed: {(stderr or stdout).strip()}")
    return _json_field("source add", stdout, "source_id")


def get_source_content(source_id: str) -> str:
    """Fetch transcript text from an NLM source.

    `nlm source content <src_id> --json` takes only the source ID (no
    notebook ID); the text is the JSON `content` field.
    """
    stdout, stderr, code = _run_nlm(["source", "content", source_id, "--json"], timeout=300)
    if code != 0:
        raise NlmError(f"nlm source content failed: {(stderr or stdout).strip()}")
    content = _json_out("source content", stdout).get("content")
    if not isinstance(content, str):
        raise NlmError(f"nlm source content returned no content: {stdout.strip()[:200]!r}")
    return content


def delete_notebook(notebook_id: str) -> None:
    """Delete a NotebookLM notebook (cleanup). `--confirm` skips its prompt."""
    stdout, stderr, code = _run_nlm(["notebook", "delete", notebook_id, "--confirm"], timeout=120)
    if code != 0:
        raise NlmError(f"nlm notebook delete failed: {(stderr or stdout).strip()}")


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
