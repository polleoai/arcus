"""On-disk output writer: success .md + .json, failure stub .md, cache check."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from .log import now_iso
from .types import ExtractionResult


_STATUS_LINE = re.compile(r"^status:\s*success\s*$", re.MULTILINE)


def _dump_yaml_frontmatter(data: dict[str, Any]) -> str:
    """Render a dict as YAML frontmatter, ordering keys per dict insertion."""
    body = yaml.safe_dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return f"---\n{body}---\n"


def write_success(out_dir: Path, slug: str, result: ExtractionResult) -> tuple[Path, Path]:
    """Write `<slug>.md` (frontmatter + body) and `<slug>.json` (full payload).

    Returns the absolute paths of the written `.md` and `.json` files.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    m = result.metadata

    fm: dict[str, Any] = {
        "source": m.source,
        "source_id": m.source_id,
        "title": m.title,
        "slug": m.slug,
    }
    if m.author:
        fm["author"] = m.author
    if m.duration_ms is not None:
        fm["duration_seconds"] = round(m.duration_ms / 1000)
    if m.posted:
        fm["posted"] = m.posted
    fm["kind"] = result.kind
    if result.extractor_detail:
        fm["extractor_detail"] = result.extractor_detail
    if m.language:
        fm["language"] = m.language
    fm["extracted_at"] = result.extracted_at
    fm["status"] = "success"

    body = f"\n# {m.title}\n\n{result.text.strip()}\n" if result.text.strip() else ""
    md = _dump_yaml_frontmatter(fm) + body
    md_path = out_dir / f"{slug}.md"
    md_path.write_text(md, encoding="utf-8")

    json_payload = {
        "status": "success",
        "kind": result.kind,
        "extractor_detail": result.extractor_detail,
        "metadata": asdict(m),
        "text": result.text,
        "segments": [asdict(s) for s in result.segments],
        "extracted_at": result.extracted_at,
    }
    json_path = out_dir / f"{slug}.json"
    json_path.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return md_path.resolve(), json_path.resolve()


def write_failure_stub(
    out_dir: Path,
    *,
    slug: str,
    source: str,
    source_id: str,
    kind: str,
    title: str | None,
    exit_code: int,
    extractor_attempted: list[str],
    error: str,
) -> None:
    """Write a stub `<slug>.md` (frontmatter-only) recording the failure."""
    out_dir.mkdir(parents=True, exist_ok=True)

    fm: dict[str, Any] = {
        "source": source,
        "source_id": source_id,
        "slug": slug,
        "kind": kind,
    }
    if title:
        fm["title"] = title
    fm["status"] = "failed"
    fm["exit_code"] = exit_code
    fm["extractor_attempted"] = extractor_attempted
    fm["error"] = error
    fm["attempted_at"] = now_iso()

    body = (
        f"\n(no body — extraction failed; rework with "
        f"`arcus --force {source}`)\n"
    )
    md = _dump_yaml_frontmatter(fm) + body
    (out_dir / f"{slug}.md").write_text(md, encoding="utf-8")

    json_payload = {
        "status": "failed",
        "kind": kind,
        "source": source,
        "source_id": source_id,
        "slug": slug,
        "title": title,
        "exit_code": exit_code,
        "extractor_attempted": extractor_attempted,
        "error": error,
        "attempted_at": fm["attempted_at"],
    }
    (out_dir / f"{slug}.json").write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def cache_hit_exists(out_dir: Path, slug: str, source_id: str) -> Path | None:
    """Return the matched `.md` `Path` for a prior success of `source_id`, else None.

    Checks both the bare form `<slug>.md` and any disambiguated forms
    `<slug>--*.md` produced by the writer's collision handler. For each
    candidate, the file's YAML frontmatter `source_id` MUST equal the
    caller's `source_id` — this prevents two different sources whose titles
    slug-collide from falsely cache-hitting each other.

    Returns the resolved `Path` of the actual matched `.md` file (which may be
    a disambiguated form, NOT the bare `<slug>.md`) so callers can report the
    real on-disk path. Returns `None` when no matching success file exists.
    """
    if not out_dir.exists():
        return None
    candidates = [out_dir / f"{slug}.md", *out_dir.glob(f"{slug}--*.md")]
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if not _STATUS_LINE.search(text):
            continue
        fm = _read_frontmatter(text)
        if fm.get("source_id") == source_id:
            return path.resolve()
    return None


def _read_frontmatter(md_text: str) -> dict[str, Any]:
    """Parse the leading YAML frontmatter block. Returns {} if absent or malformed."""
    if not md_text.startswith("---\n"):
        return {}
    end = md_text.find("\n---\n", 4)
    if end < 0:
        return {}
    block = md_text[4:end]
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
