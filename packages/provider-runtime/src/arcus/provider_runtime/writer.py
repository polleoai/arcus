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


def write_success(out_dir: Path, slug: str, result: ExtractionResult) -> None:
    """Write `<slug>.md` (frontmatter + body) and `<slug>.json` (full payload)."""
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
    (out_dir / f"{slug}.md").write_text(md, encoding="utf-8")

    json_payload = {
        "status": "success",
        "kind": result.kind,
        "extractor_detail": result.extractor_detail,
        "metadata": asdict(m),
        "text": result.text,
        "segments": [asdict(s) for s in result.segments],
        "extracted_at": result.extracted_at,
        "children": [asdict(c) for c in result.children] if result.children else [],
    }
    (out_dir / f"{slug}.json").write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


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


def cache_hit_exists(out_dir: Path, slug: str, source_id: str) -> bool:
    """True when a previously-written success file for `source_id` exists.

    Checks both the bare form `<slug>.md` and any disambiguated forms
    `<slug>--*.md` produced by the writer's collision handler. For each
    candidate, the file's YAML frontmatter `source_id` MUST equal the
    caller's `source_id` — this prevents two different sources whose titles
    slug-collide from falsely cache-hitting each other.
    """
    if not out_dir.exists():
        return False
    candidates = [out_dir / f"{slug}.md", *out_dir.glob(f"{slug}--*.md")]
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if not _STATUS_LINE.search(text):
            continue
        fm = _read_frontmatter(text)
        if fm.get("source_id") == source_id:
            return True
    return False


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
