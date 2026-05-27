# Integrating Arcus into your application

Arcus exposes **two first-class, supported integration surfaces**:

- **Python apps** use the **library API** (`arcus-provider-runtime`) — the
  pattern Athena uses in production. Start at [The core pattern](#the-core-pattern).
- **Non-Python consumers** (e.g. Peitho, a Node service) use the **CLI
  contract** — argv + an NDJSON event stream on stderr + exit codes, which Arcus
  commits to keeping semver-stable. Start at
  [Integrating via the CLI](#integrating-via-the-cli-node-and-other-non-python-consumers).

Both are fully supported; pick the one that matches your runtime.

For the conceptual overview (providers, output shapes, the single-source
contract) read [what-arcus-does.md](./what-arcus-does.md) first. For exactly what
network egress each provider needs (to sandbox extraction), see
[providers-network.md](./providers-network.md).

## Install

```bash
pip install "arcus-provider-runtime[html,pdf,office]"
```

Pick only the extras you need (`html`, `pdf`, `office`, `image`, `docling`, or
`all`). Some providers need non-Python tools on the host:

| Provider | Also requires |
|---|---|
| `html` | Chromium (`python -m playwright install chromium`) **and** `node` on `PATH` (the vendored `html2md.mjs` converter) |
| `youtube` | `yt-dlp` (`pip install yt-dlp` is pulled in by the base package; the binary must be runnable) |
| `pdf` / `docs` / `image` | nothing beyond the Python extras |

> **High-fidelity option:** installing the `[docling]` extra makes **Docling** the
> primary engine for `pdf`/`docs`/`image` — layout- and table-structure-aware
> Markdown. It's heavier (torch + models) and slower; without it those providers
> use their fast lightweight extractors. `pip install "arcus-provider-runtime[docling]"`.

> One package, `arcus-provider-runtime` (on PyPI), ships **both** the library and
> the `arcus` CLI (the CLI is pure-stdlib, so it bundles for free). Python apps use
> the library API below; non-Python consumers use the CLI contract (see
> [Integrating via the CLI](#integrating-via-the-cli-node-and-other-non-python-consumers))
> — both surfaces are supported, from the same install.

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
register_defaults(registry)          # registers youtube, pdf, docs, text, image, html
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
  .kind                # "youtube" | "pdf" | "docs" | "text" | "image" | "html"
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

### `extractor_detail` — provenance + structure signals

`extractor_detail` is a provider-specific dict. Beyond per-provider extras (e.g.
`images` for X.com), two keys carry the R4/R5 contract:

- **`structured`** (`bool`) — `True` when extraction preserved document structure
  (headings/lists/tables): the **Docling** backend (when installed) for
  pdf/docs/image, the `pymupdf4llm` tier for PDF, the pure-pip `[office]` tier
  (`openpyxl` / `python-pptx` / `python-docx`) or `pandoc` for office docs, and
  always-true for the `text` passthrough. `False` means a flattened fallback tier
  ran (e.g. `pdftotext`, or the office `zipfile` walk), so downstream
  structure-derived features (outlines from headings, tables → comparison layouts)
  are unreliable. The `extractor` field names the exact tier that ran (e.g.
  `"openpyxl"`, `"python-pptx"`, `"pandoc"`, `"zipfile"`, `"docling"`).
- **`locators`** (`list`) — source positions parallel to `segments`, so each
  `segments[i]` is traceable back to the original. Shape:
  `[{"segment": <int index into segments>, "<unit>": <value>}, …]` where `<unit>`
  is `"page"` (PDF and any Docling-extracted source, 1-indexed), `"sheet"` (xlsx
  fallback, sheet name), or `"slide"` (pptx fallback, 1-indexed slide number). The
  Docling backend emits `"page"` locators uniformly across pdf/docs/image. Empty
  for providers/tiers with no discrete unit (e.g. HTML, and flattened fallbacks
  like `pdftotext` or docx).

```python
payload["extractor_detail"]["structured"]   # True | False
payload["extractor_detail"]["locators"]      # e.g. [{"segment": 0, "page": 1}, ...]
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
        kind=payload["kind"],                 # youtube|pdf|docs|text|image|html
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

## Integrating via the CLI (Node and other non-Python consumers)

If your app isn't Python, you integrate by spawning the `arcus` CLI as a
subprocess and reading its NDJSON event stream. This is a **first-class,
supported integration surface** — not a fallback. The three things you bind to:

1. the **argv shape** (`arcus <input> --out <dir> --json-log --keep-intermediates`),
2. the **NDJSON event schema** emitted on stderr, and
3. the **exit codes** (see the [exit-codes table](#exit-codes-exit_codes)),

are a **semver-stable contract**. Arcus commits to keeping them backward
compatible within a major version: events and keys may be *added*, but existing
event names, the keys documented below, their snake_case spelling, and exit-code
meanings will not change or be removed without a major bump.

Install the CLI with `pipx install arcus-provider-runtime` (the `arcus` binary
ships inside that one package).

### Spawn command

```bash
arcus <input> --out <dir> --json-log --keep-intermediates
```

- `<input>` is **generic** — a URL *or* a local file path. The provider is
  auto-detected; you do not tell Arcus which kind it is.
- `--out <dir>` is where `<slug>.md` + `<slug>.json` are written.
- `--json-log` turns on the NDJSON event stream (below).
- `--keep-intermediates` keeps any intermediate artifacts on disk for
  inspection / debugging.

### The NDJSON event schema

With `--json-log`, Arcus writes **one JSON object per line to stderr** (the same
lines are also appended to `<out_dir>/.log/extract-log.ndjson`). stdout is not
part of this contract — read **stderr** line-by-line and `JSON.parse` each line.

Every event has:

- **`event`** — the single discriminator key. One of:
  `started`, `detected`, `fetching`, `extracting`, `cache_hit`, `success`, `failed`.
  (There is **no** `status` key in the event stream.)
- **`ts`** — ISO-8601 UTC timestamp string.

Per-event fields (all keys are **snake_case** — `source_id`, `md_path`,
`json_path` — map them to your own camelCase):

| `event` | Fields | Terminal? | Notes |
|---|---|---|---|
| `started` | `raw` | no | `raw` is the input string you passed. |
| `detected` | `kind`, `source_id` | no | Provider chosen. **Not guaranteed exactly once** — a non-fatal predict-slug warning is also emitted as a `detected` event carrying extra `warning:"predict_slug_failed"` + `error`. |
| `fetching` | `kind`, `source_id` | no | Progress between detection and the terminal event. Not all providers emit this (local files skip `fetching`). |
| `extracting` | `kind`, `source_id` | no | Progress; likewise not always emitted. |
| `cache_hit` | `kind`, `source_id`, `slug`, `md_path`, `json_path` | **yes** | Result served from cache. `md_path`/`json_path` are **absolute** and point at the cached files. |
| `success` | `kind`, `source_id`, `slug`, `md_path`, `json_path` | **yes** | Freshly extracted. `md_path`/`json_path` are **absolute** paths to the just-written files. |
| `failed` | `kind`, `source_id`, `slug?`, `error` | **yes** | The `error` string describes the failure; the **process exit code** conveys the failure *class* (retryable vs permanent — see exit codes). The "no provider matched" failure carries `raw` + `error` instead of `kind`/`source_id`. |

`kind` ∈ `youtube | pdf | docs | text | image | html`.

Treat `success`, `cache_hit`, and `failed` as the terminal events: capture the
last one you see, then reconcile with the process exit code on close.

### Worked Node example

`child_process.spawn` with no shell, reading stderr line-by-line:

```javascript
import { spawn } from "node:child_process";
import * as readline from "node:readline";

export function runArcus({ input, outDir }, onProgress) {
  return new Promise((resolve) => {
    const proc = spawn(
      "arcus",
      [input, "--out", outDir, "--json-log", "--keep-intermediates"],
      { stdio: ["ignore", "ignore", "pipe"] } // NDJSON events on stderr
    );

    // `spawn` itself failing (e.g. `arcus` not on PATH → ENOENT) surfaces here,
    // not on the `close` event. Without this handler Node throws and the
    // promise hangs forever.
    proc.on("error", (err) => {
      resolve({ status: "failed", exitCode: null, error: err.message });
    });

    const rl = readline.createInterface({ input: proc.stderr });
    let terminal = null;
    rl.on("line", (line) => {
      let ev;
      try {
        ev = JSON.parse(line);
      } catch {
        return; // skip any non-JSON line
      }
      onProgress?.(ev); // ev.event ∈ started|detected|fetching|extracting|cache_hit|success|failed
      if (["success", "cache_hit", "failed"].includes(ev.event)) {
        terminal = ev; // keep the last terminal event
      }
    });

    proc.on("close", (code) => {
      if (code === 0 && terminal && terminal.event !== "failed") {
        resolve({
          status: "success",
          exitCode: code,
          kind: terminal.kind,
          sourceId: terminal.source_id, // snake_case → camelCase
          slug: terminal.slug,
          mdPath: terminal.md_path,
          jsonPath: terminal.json_path,
        });
      } else {
        resolve({
          status: "failed",
          exitCode: code, // the failure CLASS — see exit codes
          error: terminal?.error ?? `arcus exited ${code}`,
        });
      }
    });
  });
}
```

After a successful run, read `mdPath` / `jsonPath` (both absolute) for the
markdown body and the structured payload — the same `<slug>.json` shape the
library returns.

### Classifying failures (retry vs give up)

Branch on the **exit code**, not on the `error` string. The
[exit-codes table](#exit-codes-exit_codes) is the contract; the load-bearing
distinction for a consumer is:

- `RATE_LIMITED` (**41**) → **retryable**: back off and try again later.
- `VIDEO_RESTRICTED` (**40**) → **permanent**: the source is private / age- /
  region-locked; do not retry.

(Other non-zero codes — `10`, `11`, `20`, `21`, `30` — are likewise permanent
for a given input; `21` means an external tool needs auth, e.g. `nlm login`.)

### Quick / terminal-use invocations

The same binary is handy for ad-hoc checks:

```bash
arcus <input>               # writes <slug>.md + .json to ./out/
arcus <input> --out /path   # choose the output dir
arcus --probe <input>       # show which provider would run (no extraction)
arcus --check               # tool/auth environment status
arcus --list-providers      # registered provider kinds
arcus <input> --force       # re-extract even on cache hit
```

## What Arcus will NOT do for you

- **No multi-source / crawl / recursion.** Loop at your layer; call once per source.
- **No storage / dedup / cross-referencing / synthesis.** That's the consumer's job.
- **No auth management** beyond what a provider's tool needs (e.g. `nlm login`
  for the NLM YouTube fallback).

Keep that boundary and Arcus stays a stable, swappable kernel under your app.
