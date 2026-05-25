# Integrating Arcus into your application

This guide shows how to consume `arcus-provider-runtime` as a library. It's the
same pattern Athena uses in production and the one Peitho should follow.

For the conceptual overview (providers, output shapes, the single-source
contract) read [what-arcus-does.md](./what-arcus-does.md) first.

## Install

```bash
pip install "arcus-provider-runtime[html,pdf,office]"
```

Pick only the extras you need (`html`, `pdf`, `office`, or `all`). Some
providers need non-Python tools on the host:

| Provider | Also requires |
|---|---|
| `html` | Chromium (`python -m playwright install chromium`) **and** `node` on `PATH` (the vendored `html2md.mjs` converter) |
| `youtube` | `yt-dlp` (`pip install yt-dlp` is pulled in by the base package; the binary must be runnable) |
| `pdf` / `docs` | nothing beyond the Python extras |

> The **library** is `arcus-provider-runtime` (on PyPI). The standalone `arcus`
> **CLI** lives in the repo (`uv tool install ./packages/cli`) and is for
> terminal use — applications integrate against the library API below, not the
> CLI.

## The core pattern

`Factory.run()` does detect → cache-check → extract → write, and **returns an
exit code** (`int`). It writes `<slug>.md` + `<slug>.json` into the `out_dir`
you pass; you read those files for the result.

```python
import json
import tempfile
from pathlib import Path

from arcus.provider_runtime import (
    EXIT_CODES, Factory, ProviderRegistry, register_defaults,
)

# Build the factory ONCE and reuse it across many extractions.
registry = ProviderRegistry()
register_defaults(registry)          # registers youtube, pdf, docs, html
factory = Factory(registry)


def extract(source: str) -> dict:
    """Extract one URL/file path. Returns arcus's JSON payload dict.
    Raises RuntimeError on failure."""
    # Optional cheap probe: which provider would handle this? (no network)
    match = factory.detect(source)   # -> (provider, detection) | None
    if match is None:
        raise RuntimeError(f"no arcus provider matches: {source}")

    with tempfile.TemporaryDirectory(prefix="arcus-") as tmp:
        out_dir = Path(tmp)
        exit_code = factory.run(source, out_dir=out_dir, force=False)
        if exit_code != EXIT_CODES["SUCCESS"]:
            raise RuntimeError(f"arcus extraction failed (exit {exit_code}): {source}")
        payload = json.loads(next(out_dir.glob("*.json")).read_text(encoding="utf-8"))
    return payload
```

Then use the payload:

```python
payload  = extract("https://www.youtube.com/watch?v=…")
text     = payload["text"]                              # markdown body
meta     = payload["metadata"]                          # source/title/slug/author/…
segments = payload["segments"]                          # [{start_ms,end_ms,text}, …]
images   = payload.get("extractor_detail", {}).get("images", [])
```

### Notes that matter in practice

- **Reuse the factory.** Construction is cheap but registering providers each
  call is wasted work; build it once at startup.
- **`out_dir` is yours to choose.** Pass a real cache directory if you want
  Arcus's built-in cache-hit behavior across runs; pass a `TemporaryDirectory`
  (as above) if you only want the in-memory payload and will persist it
  yourself.
- **`force=True`** re-extracts even on a cache hit.
- **Arcus is chatty on stderr/stdout** (Playwright, yt-dlp). If that pollutes
  your output, redirect FDs around the `factory.run()` call (Athena wraps it in
  a `_silence_fds()` context manager).
- **`factory.run` never raises** for provider failures — it returns a non-zero
  exit code and writes a `status: failed` stub. Check the exit code.

## Types reference

All importable from `arcus.provider_runtime`:

```python
ExtractionResult       # what a provider returns internally (mirrors the .json)
  .status              # "success" | "failed"
  .kind                # "youtube" | "pdf" | "docs" | "html"
  .extractor_detail    # dict — provider-specific (e.g. {"images": [...]})
  .metadata            # SourceMetadata
  .text                # str — the markdown body
  .segments            # list[Segment]
  .extracted_at        # ISO-8601 str
  .error / .exit_code  # set on failure

SourceMetadata
  .source .source_id .title .slug
  .author .duration_ms .posted .language   # optional, may be None

Segment(start_ms, end_ms, text)            # frozen
DetectionResult(kind, source_id, raw, metadata)
```

### Exit codes (`EXIT_CODES`)

| Name | Code | Meaning |
|---|---|---|
| `SUCCESS` | 0 | extracted (or cache hit) |
| `INVALID_ARGS` | 2 | bad arguments |
| `PROVIDER_PRIMARY_FAILED` | 10 | the matched provider's primary path failed |
| `PROVIDER_FORCED_NO_MATCH` | 11 | a forced provider didn't match |
| `PROVIDER_FALLBACK_FAILED` | 20 | the fallback path also failed |
| `TOOL_NOT_AUTHENTICATED` | 21 | an external tool needs auth (e.g. NLM) |
| `EXTRACTORS_EXHAUSTED` | 30 | no provider matched the input |
| `VIDEO_RESTRICTED` | 40 | private / age- / region-locked video |
| `RATE_LIMITED` | 41 | upstream rate limit |

## A worked example: wiring Arcus into Peitho

Peitho turns one or more sources into presentations. Arcus is its ingest layer:
each source URL/file becomes normalized text + metadata that Peitho maps into
its ContentIR.

```python
# peitho/ingest/arcus_source.py
import json, tempfile
from pathlib import Path
from arcus.provider_runtime import EXIT_CODES, Factory, ProviderRegistry, register_defaults

_registry = ProviderRegistry()
register_defaults(_registry)
_FACTORY = Factory(_registry)            # module-level singleton


def load_source(source: str) -> "SourceDoc":
    """Extract one URL/file and adapt it into Peitho's SourceDoc.
    Single source per call — Peitho's pipeline loops for multi-source decks."""
    with tempfile.TemporaryDirectory(prefix="peitho-arcus-") as tmp:
        out = Path(tmp)
        code = _FACTORY.run(source, out_dir=out, force=False)
        if code != EXIT_CODES["SUCCESS"]:
            raise IngestError(f"arcus failed (exit {code}) for {source}")
        payload = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))

    md = payload["metadata"]
    return SourceDoc(
        title=md["title"],
        body_markdown=payload["text"],
        author=md.get("author"),
        kind=payload["kind"],                 # youtube|pdf|docs|html
        # timestamps let Peitho deep-link slides back to a video moment:
        segments=payload["segments"],
        images=payload.get("extractor_detail", {}).get("images", []),
        source_url=md["source"],
    )
```

That single adapter gives Peitho YouTube transcripts, PDFs, office docs, and web
articles through one code path. The flagship "ingest a YouTube transcript / mix
several videos into one deck" demo is exactly: call `load_source()` per video,
then synthesize across the returned `SourceDoc`s in Peitho's own layer — Arcus
stays single-source, Peitho owns the multi-source synthesis.

## Standalone CLI (optional)

For terminal use or quick checks (not the integration path):

```bash
arcus <url>                 # writes <slug>.md + .json to ./out/
arcus <url> --out /path     # choose the output dir
arcus --probe <url>         # show which provider would run (no extraction)
arcus --check               # tool/auth environment status
arcus --list-providers      # registered provider kinds
arcus <url> --force         # re-extract even on cache hit
```

## What Arcus will NOT do for you

- **No multi-source / crawl / recursion.** Loop at your layer; call once per source.
- **No storage / dedup / cross-referencing / synthesis.** That's the consumer's job.
- **No auth management** beyond what a provider's tool needs (e.g. `nlm login`
  for the NLM YouTube fallback).

Keep that boundary and Arcus stays a stable, swappable kernel under your app.
