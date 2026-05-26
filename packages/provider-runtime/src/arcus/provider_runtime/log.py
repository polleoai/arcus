"""NDJSON event logger.

Every arcus invocation appends structured events to
`<out_dir>/.log/extract-log.ndjson`. Consumers parse this for
audit logging. Optionally mirrors to stderr (`--json-log` flag)
so subprocess callers can read events without touching disk.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    """UTC ISO-8601 timestamp like '2026-05-17T19:42:01.123456+00:00'."""
    return datetime.now(timezone.utc).isoformat()


# The complete NDJSON event vocabulary. The terminal events are
# cache_hit / success / failed; the rest are progress events. The single
# discriminator key on every event is `event`.
STAGES: tuple[str, ...] = (
    "started",
    "detected",
    "fetching",
    "extracting",
    "cache_hit",
    "success",
    "failed",
)


class EventLogger:
    """Append-only NDJSON event logger."""

    def __init__(self, out_dir: Path, *, json_log_stderr: bool = False) -> None:
        self.out_dir = out_dir
        self.json_log_stderr = json_log_stderr
        self._log_dir = out_dir / ".log"
        self._log_file = self._log_dir / "extract-log.ndjson"
        self._dir_ready = False

    def emit(self, event: dict[str, Any]) -> None:
        """Append a single event. Creates the .log directory lazily."""
        line = json.dumps(event, ensure_ascii=False) + "\n"

        if self.json_log_stderr:
            sys.stderr.write(line)
            sys.stderr.flush()

        if not self._dir_ready:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._dir_ready = True

        with self._log_file.open("a", encoding="utf-8") as f:
            f.write(line)

    def stage(self, event: str, **fields: Any) -> None:
        """Emit one event under the uniform `event` discriminator.

        `event` must be one of STAGES. Always stamps `ts`. Extra keyword
        fields (kind, source_id, slug, md_path, json_path, error, ...) ride
        along verbatim.
        """
        if event not in STAGES:
            raise ValueError(f"unknown stage {event!r}; must be one of {STAGES}")
        record = {"ts": now_iso(), "event": event}
        record.update(fields)
        self.emit(record)
