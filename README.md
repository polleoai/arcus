# Arcus

Content extraction kernel: give it one URL or file path, get normalized markdown
+ structured metadata. MIT licensed, on PyPI.

Providers: **YouTube** (transcripts), **PDF**, **office docs** (docx/pptx/xlsx/epub),
**Markdown/text**, **images** (OCR via Tesseract), and **HTML** (Playwright-rendered,
incl. SPAs + X.com). One `Factory.run()` API; one source in, one result out.

## Documentation

- [What Arcus does](docs/what-arcus-does.md) — providers, output shapes, the single-source contract
- [Integration guide](docs/integration-guide.md) — install, the library API, the CLI contract (Node/subprocess), types, and a worked Peitho example
- [Per-provider network behavior](docs/providers-network.md) — per-provider network egress, for sandboxing extraction

## Install

One package ships **both** the library and the `arcus` CLI.

**As a library** (import path — see the integration guide):

```bash
pip install "arcus-provider-runtime[html,pdf,office]"
```

**As a CLI** (the `arcus` binary — for Node/subprocess consumers and terminal use):

```bash
pipx install arcus-provider-runtime    # puts the `arcus` command on PATH
arcus --version
```

**For development** (from source):

```bash
git clone git@github.com:polleoai/arcus.git ~/Projects/arcus
cd ~/Projects/arcus
uv sync --all-packages --all-extras
uv tool install ./packages/provider-runtime    # global `arcus` binary
arcus --version
```

Requires:
- Python 3.11+
- `yt-dlp` (`brew install yt-dlp`) — for YouTube provider
- `nlm` CLI authenticated via `nlm login` — for the NLM fallback

> **Note:** the `arcus` CLI is pure-stdlib and ships *inside* `arcus-provider-runtime`, so there is no separate CLI package — `pip`/`pipx install arcus-provider-runtime` gives both the library and the command. `uv run arcus --version` works from inside a checkout without a global binary.

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
| 11 | a forced provider didn't match the input |
| 20 | provider's fallback failed |
| 21 | external tool not authenticated |
| 30 | no provider matched / all exhausted |
| 40 | video private / age-locked / region-locked (permanent — don't retry) |
| 41 | upstream rate limit (retryable — back off and retry) |

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

Single `Factory.run()` entry point; pluggable providers under `packages/provider-runtime/src/arcus/provider_runtime/providers/<kind>/`. Each provider implements a small `matches` / `predict_slug` / `extract` contract. See [What Arcus does](docs/what-arcus-does.md) and the [integration guide](docs/integration-guide.md).

## Development

```bash
uv sync --all-packages --all-extras
uv run pytest                                    # full test suite
uv run pytest packages/provider-runtime/tests/cli  # CLI only
uv run arcus --version
```

Roadmap and known issues are tracked in [GitHub issues](https://github.com/polleoai/arcus/issues).
