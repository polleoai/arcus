# arcus

Content extraction kernel: turn a URL or path into a normalized transcript on disk.

v1 supports YouTube (yt-dlp captions → NotebookLM fallback). HTML / PDF / Athena-Topic providers land in Plan A.1.

## Install

```bash
git clone git@github.com:jivebug/arcus.git ~/Projects/arcus
cd ~/Projects/arcus
uv sync --all-packages --all-extras
uv tool install ./packages/cli
arcus --version
```

Requires:
- Python 3.11+
- `yt-dlp` (`brew install yt-dlp`) — for YouTube provider
- `nlm` CLI authenticated via `nlm login` — for the NLM fallback

> **Note:** `uv tool install` is uv's pipx equivalent. The CLI workspace package depends on the in-tree `arcus-provider-runtime` via `tool.uv.sources`, so installation through `pipx` (which doesn't read `tool.uv.sources`) will fail to resolve. If you don't want a global binary, `uv run arcus --version` works from inside the checkout.

## Use

```bash
arcus <youtube-url>                # writes <slug>.md + .json to ./out/
arcus <url> --out /path/to/dir
arcus <url> --force                # re-extract even if cached
arcus --probe <url>                # show which provider would run
arcus --check                      # tool environment + auth status
arcus --list-providers             # show registered provider kinds
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success / cache hit |
| 2 | invalid args |
| 10 | provider's primary path failed |
| 20 | provider's fallback failed |
| 21 | external tool not authenticated |
| 30 | no provider matched / all exhausted |
| 40 | video private / age-locked / region-locked |

## Files written

```
out/
  <slug>.md         frontmatter + readable body
  <slug>.json       structured payload (segments, timing, provenance)
  .log/
    extract-log.ndjson    every event from every run
```

Failed runs leave a stub `.md` with `status: failed` + the URL + a retry hint, so no work is lost on disk.

## Architecture

Mirrors gryphon's `provider-runtime` pattern. Single `Factory.run()` entry point; pluggable providers under `packages/provider-runtime/src/arcus/provider_runtime/providers/<kind>/`. See `docs/specs/2026-05-17-arcus-provider-runtime-design.md` for the full design.

## Development

```bash
uv sync --all-packages --all-extras
uv run pytest                       # full test suite
uv run pytest packages/cli/tests    # CLI only
uv run arcus --version
```

Plans live in `docs/plans/`. Plan A.0 (this release) ships the provider-runtime kernel + the YouTube provider. Plan A.1 adds HTML, PDF, and Athena-Topic providers; Plan A.2 migrates athena to consume arcus.
