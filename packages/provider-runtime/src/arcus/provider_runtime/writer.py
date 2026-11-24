"""On-disk output writer: success .md + .json, failure stub .md, cache check."""

from __future__ import annotations

import hashlib
import json
import shutil
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from .log import now_iso
from .types import ExtractionResult


_STATUS_LINE = re.compile(r"^status:\s*success\s*$", re.MULTILINE)
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


class SlugTakenError(Exception):
    """An output stem already holds another source's extraction.

    Raised by the writers instead of overwriting it (#14)."""

    def __init__(self, slug: str, owner: str | None, source_id: str) -> None:
        self.slug, self.owner, self.source_id = slug, owner, source_id
        held = f"source {owner!r}" if owner else "a file arcus cannot attribute"
        super().__init__(
            f"output {slug!r} already holds {held}; not overwriting it with {source_id!r}")


_UNKNOWN = object()


def _slug_owner(out_dir: Path, slug: str):
    """The source_id whose output occupies `<slug>`: None when the stem is
    free (no .md and no .json), _UNKNOWN when files exist but name no source."""
    md, js = out_dir / f"{slug}.md", out_dir / f"{slug}.json"
    if md.is_file():
        try:
            sid = _read_frontmatter(md.read_text(encoding="utf-8")).get("source_id")
        except (OSError, UnicodeDecodeError):
            sid = None
        if sid is not None:
            return str(sid)
    if js.is_file():
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
            sid = data.get("source_id") or (data.get("metadata") or {}).get("source_id")
        except (OSError, ValueError, AttributeError):
            sid = None
        if sid is not None:
            return str(sid)
    if md.exists() or js.exists():
        return _UNKNOWN
    return None


def _check_owner(out_dir: Path, slug: str, source_id: str) -> None:
    owner = _slug_owner(out_dir, slug)
    if owner is not None and owner != source_id:
        raise SlugTakenError(slug, None if owner is _UNKNOWN else owner, source_id)


def claim_slug(out_dir: Path, slug: str, source_id: str) -> str:
    """Return the output stem for `source_id`: one no other source holds.

    The bare `slug` when it is free or already this source's; otherwise
    `<slug>--<tag>`, where the tag is `source_id[:8]` for a filename-safe id
    (a YouTube video id) and a hash of it otherwise (paths and URLs), then the
    full id / full hash. A re-run of a source lands on the name it had.
    """
    if _SAFE_ID.fullmatch(source_id):
        tags = [source_id[:8], source_id]
    else:
        digest = hashlib.sha256(source_id.encode("utf-8", "surrogatepass")).hexdigest()
        tags = [digest[:10], digest]
    candidates = list(dict.fromkeys([slug, *(f"{slug}--{t}" for t in tags)] if slug else tags))
    owners = {stem: _slug_owner(out_dir, stem) for stem in candidates}
    # A name this source already holds wins over a free one, so a source never
    # ends up with two outputs (e.g. after the bare name's owner was deleted).
    for stem in candidates:
        if owners[stem] == source_id:
            return stem
    for stem in candidates:
        if owners[stem] is None:
            return stem
    raise SlugTakenError(candidates[-1], None, source_id)


def _dump_yaml_frontmatter(data: dict[str, Any]) -> str:
    """Render a dict as YAML frontmatter, ordering keys per dict insertion."""
    body = yaml.safe_dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return f"---\n{body}---\n"


def _figures_dir(out_dir: Path, slug: str) -> Path:
    return out_dir / f"{slug}.figures"


def _replace_assets(out_dir: Path, slug: str, assets) -> None:
    """Replace `<slug>.figures/` with exactly `assets` (removes stale figures
    from an earlier extraction of the same slug; no dir when there are none).
    Asset paths must live directly under that directory."""
    fig_dir = _figures_dir(out_dir, slug)
    if fig_dir.is_dir() and not fig_dir.is_symlink():
        shutil.rmtree(fig_dir)
    elif fig_dir.is_symlink() or fig_dir.exists():
        fig_dir.unlink()
    if not assets:
        return
    fig_dir.mkdir(parents=True)
    root = fig_dir.resolve()
    for a in assets:
        target = (out_dir / a.path).resolve()
        if target.parent != root:
            raise ValueError(f"asset path outside {fig_dir.name}/: {a.path!r}")
        target.write_bytes(a.data)


def write_success(out_dir: Path, slug: str, result: ExtractionResult) -> tuple[Path, Path]:
    """Write `<slug>.md` (frontmatter + body) and `<slug>.json` (full payload).

    Returns the absolute paths of the written `.md` and `.json` files.
    """
    m = result.metadata
    _check_owner(out_dir, slug, m.source_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    _replace_assets(out_dir, slug, result.assets)

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

    stripped = result.text.strip()
    if not stripped:
        body = ""
    elif re.match(r"^#{1,6}\s", stripped):  # body already opens with its own heading
        body = f"\n{stripped}\n"
    else:
        body = f"\n# {m.title}\n\n{stripped}\n"
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
    """Write a stub `<slug>.md` (frontmatter-only) recording the failure.

    Raises SlugTakenError rather than replace another source's output."""
    _check_owner(out_dir, slug, source_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    _replace_assets(out_dir, slug, [])

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
