"""Title-to-slug conversion with collision disambiguation."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def make_slug(title: str, max_len: int = 80) -> str:
    """Convert a title to a filesystem-safe slug.

    Steps:
      1. NFKD normalize, strip combining marks (diacritics).
      2. ASCII-encode, dropping anything that can't represent.
      3. Lowercase.
      4. Replace runs of non-alphanumeric with '-'.
      5. Strip leading/trailing '-'.
      6. Truncate to `max_len` on a whole-word boundary.

    Returns empty string for inputs that have no representable characters.
    """
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_bytes = decomposed.encode("ascii", errors="ignore").decode("ascii")
    lowered = ascii_bytes.lower()
    dashed = _NON_ALNUM.sub("-", lowered).strip("-")

    if len(dashed) <= max_len:
        return dashed

    # Whole-word truncate: cut at the last dash before max_len.
    truncated = dashed[:max_len]
    last_dash = truncated.rfind("-")
    if last_dash > 0:
        truncated = truncated[:last_dash]
    return truncated.rstrip("-")


def disambiguate(slug: str, source_id: str, out_dir: Path) -> str:
    """Return a unique filename stem (no extension) for this slug in `out_dir`.

    If `slug` is empty, falls back to `source_id` directly.
    Otherwise: if `<slug>.md` doesn't exist, returns the bare slug.
    On collision, appends `--<source_id[:8]>`; on a further collision
    (rare), appends the full source_id.
    """
    if not slug:
        return source_id

    if not (out_dir / f"{slug}.md").exists():
        return slug

    short = f"{slug}--{source_id[:8]}"
    if not (out_dir / f"{short}.md").exists():
        return short

    full = f"{slug}--{source_id}"
    return full
