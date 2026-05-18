"""NLM notebook name length probe + truncation helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


FALLBACK_LIMIT = 200
_NATURAL_BREAKS = {":", "—", "|", ",", "。"}


def truncate_for_notebook_name(title: str, limit: int) -> str:
    """Truncate a title to fit `limit` codepoints with a natural break if possible."""
    cps = list(title)
    if len(cps) <= limit:
        return title

    budget = limit - 1  # reserve for "…"
    natural_start = int(budget * 0.8)

    for i in range(budget, natural_start - 1, -1):
        if cps[i] in _NATURAL_BREAKS:
            return "".join(cps[: i + 1]).rstrip() + "…"

    for i in range(budget, 0, -1):
        if cps[i].isspace():
            return "".join(cps[:i]).rstrip() + "…"

    return "".join(cps[:budget]) + "…"


def load_cached_limit(path: Path) -> int:
    """Load the NLM name length limit from cache; fall back to FALLBACK_LIMIT."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        limit = data.get("notebook_name_limit")
        if isinstance(limit, int) and limit > 0:
            return limit
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return FALLBACK_LIMIT


def save_cached_limit(path: Path, limit: int) -> None:
    """Persist the discovered NLM name length limit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "notebook_name_limit": limit,
        "probed_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_notebook_name(
    *,
    title: str,
    video_id: str,
    date: str,
    limit: int,
    tag: str | None = None,
) -> str:
    """Format the NLM notebook name: arcus[<tag>] • <title> • <video_id> • <date>."""
    tag_part = f"[{tag}] " if tag else ""
    fixed = f"arcus{tag_part} •  • {video_id} • {date}"
    title_budget = max(20, limit - len(fixed))
    truncated = truncate_for_notebook_name(title, title_budget)
    return f"arcus{tag_part} • {truncated} • {video_id} • {date}"
