# Requirements — YouTube transcript extraction must not silently depend on a JS runtime

- **Status:** Open — ready for implementation
- **Filed:** 2026-06-02
- **Reporter:** Athena integration (cross-platform VM matrix surfaced it)
- **Component:** `arcus.provider_runtime.providers.youtube`
- **Severity:** High — silent failure (success-shaped empty transcript) in a shipped product
- **Affects:** `arcus-provider-runtime` ≥ 0.6.0 running against `yt-dlp` ≥ ~2026.3.x

---

## 1. Summary

arcus's YouTube provider extracts transcripts via `yt-dlp`. Recent `yt-dlp`
releases (observed: **2026.3.17**) deprecated YouTube extraction without a
JavaScript runtime: the web player client now requires a JS engine (deno by
default) to solve the player challenge, and **without one the auto-caption
tracks are not returned at all.** arcus configures no JS runtime and pins
`yt-dlp` with no upper bound, so on any machine that lacks `deno` (or `node`
explicitly enabled) and runs current `yt-dlp`, **caption extraction silently
returns empty.** arcus then falls through to the NotebookLM (NLM) fallback,
which is unauthenticated in virtually every environment, and ultimately emits a
transcript-less result with **no diagnostic explaining why**.

The result is a success-shaped empty transcript: the consumer (Athena) gets a
metadata-only raw with no body, and no signal that a fixable prerequisite (a JS
runtime) was missing.

## 2. How we found it

Athena's cross-platform VM test matrix runs a real-network `kb add <youtube>`
acceptance test (AT-4: "Me at the zoo", `jNQXAC9IVRw`). Results:

| Environment | `yt-dlp` | JS runtime present | AT-4 (youtube → transcript → wiki) |
|---|---|---|---|
| macOS host | 2026.3.17 | **deno 2.7.11** (Homebrew, incidental) | ✅ pass — deno solves the challenge, captions returned |
| Linux VM (days earlier) | older snapshot (unpinned) | none | ✅ pass — pre-requirement `yt-dlp` still returned captions JS-free |
| Windows VM (today) | 2026.3.17 | node present, **no deno** | ❌ fail — "no JS runtime" → empty captions → empty transcript |

The macOS and Linux passes are **environmental luck**, not correctness: macOS is
masked by an incidentally-installed `deno`; Linux is masked by an older `yt-dlp`
snapshot. The same unpinned dependency on a fresh Linux box today pulls
2026.3.17 and fails identically. **Windows is the canary, not the exception.**

Direct reproduction (any machine without `deno`, current `yt-dlp`):

```console
$ python -m yt_dlp --skip-download --write-auto-subs --sub-langs en \
    -o zoo "https://www.youtube.com/watch?v=jNQXAC9IVRw"
[youtube] Extracting URL: https://www.youtube.com/watch?v=jNQXAC9IVRw
[youtube] jNQXAC9IVRw: Downloading webpage
WARNING: [youtube] No supported JavaScript runtime could be found. Only deno is
         enabled by default; to use another runtime add --js-runtimes RUNTIME[:PATH].
         YouTube extraction without a JS runtime has been deprecated, and some
         formats may be missing.
[youtube] jNQXAC9IVRw: Downloading android vr player API JSON   ← JS-free fallback client
[info] jNQXAC9IVRw: Downloading 1 format(s): 18
[info] There are no subtitles for the requested languages       ← captions silently absent
```

The same command on a machine with `deno` on PATH returns the `.en.vtt` caption
file normally.

## 3. Root cause (exact code path)

The silent-degradation chain, file by file:

1. **`providers/youtube/ytdlp_adapter.py:51` — `fetch_metadata()`** builds yt-dlp
   options with **no** `player_client`, `extractor_args`, or JS-runtime
   configuration:

   ```python
   opts = {"skip_download": True, "quiet": True, "no_warnings": True}
   ```

   On `yt-dlp` 2026.3.x with no JS runtime, yt-dlp falls back to the
   `android_vr` client, whose player response carries **no `automatic_captions`**.
   (`no_warnings: True` also suppresses the very warning that would have told us
   why.)

2. **`ytdlp_adapter.py:86` — `parse_subtitles_from_info()`** reads
   `info["automatic_captions"]`, which is now empty → returns `[]`.

3. **`providers/youtube/youtube.py:111`** — `if meta.subtitle_tracks:` is
   `False`, so **the entire captions path is skipped with no error.**

4. **`youtube.py:151`** — control falls through to the **NLM fallback**, which
   calls `check_auth()` (`youtube.py:153`). Unauthenticated in CI / fresh
   installs → `_failure(TOOL_NOT_AUTHENTICATED)`. The surfaced error names NLM
   auth, **not** the real cause (missing JS runtime → no captions).

5. Consumer (Athena) receives a transcript-less result and writes a
   metadata-only raw; downstream summary/wiki has nothing to work with.

The defect is **two-fold**: (a) arcus does not ensure yt-dlp has a usable JS
runtime even when one (`node`) is available, and (b) when captions are
unavailable, arcus degrades **silently** instead of reporting the actionable
reason.

## 4. Requirements

### R1 — Ensure yt-dlp uses an available JS runtime (functional fix)

arcus MUST configure yt-dlp to use a JavaScript runtime that is **already present
on the host** for YouTube extraction, rather than relying on yt-dlp's deno-only
default. arcus **detects and uses** an existing runtime; it does **not** install
one (see §6 — provisioning is the environment's responsibility). The key
consequence: on a host that already has a JS runtime, this fix requires **no new
dependency, no new install, and no environment change** — arcus simply tells
yt-dlp to use the runtime that is already there.

- **R1.1** — Detect a usable JS runtime already on PATH at extraction time and
  pass it to yt-dlp. **Prefer `node`; fall back to `deno`.** Rationale for
  node-first: the `[html]` provider **already requires `node` on PATH at runtime**
  (`html2md.mjs`), so on any install with `arcus[html]` a JS runtime is
  **already present** and R1 is satisfied with zero new provisioning. yt-dlp
  auto-enables only `deno` by default — which is *why* an explicit option is
  required even when `node` is sitting right there on PATH (the exact situation
  observed on the Windows test guest: `node` present, but yt-dlp ignored it).
- **R1.2** — Pass the detected runtime to yt-dlp through the option it exposes
  for this (CLI: `--js-runtimes RUNTIME[:PATH]`; confirm the equivalent
  `YoutubeDL` Python-API option key against the pinned yt-dlp version — likely a
  top-level `js_runtimes` option and/or `extractor_args={"youtube": {...}}`).
  Apply it to **both** `fetch_metadata()` and `fetch_captions()` so metadata
  enumeration and caption download use the same client.
- **R1.3** — Detection MUST be cached per process (mirror the existing
  `_METADATA_CACHE` pattern in `youtube.py`) so it doesn't re-probe PATH on every
  call.
- **R1.4** — If no JS runtime is found, do **not** crash; proceed but trigger R2.

### R2 — Make caption-extraction failure LOUD, not silent (observability fix)

When captions cannot be obtained, arcus MUST surface an actionable, specific
reason instead of a generic transcript-less success or a misleading NLM-auth
error.

- **R2.1** — Stop suppressing the diagnostic: set `no_warnings: False` (or
  capture yt-dlp's logger) on the metadata/caption calls so the "No supported
  JavaScript runtime" signal is observable, and route it into arcus's own log.
- **R2.2** — Introduce a distinct, documented failure reason/exit code for
  "captions unavailable because no JS runtime is configured" — e.g.
  `PROVIDER_PRECONDITION_MISSING` (or extend an existing `EXIT_CODES` entry).
  It MUST be distinguishable from `VIDEO_RESTRICTED`, `TOOL_NOT_AUTHENTICATED`
  (NLM), and `PROVIDER_PRIMARY_FAILED`.
- **R2.3** — The error message MUST name the remedy: install/enable a JS runtime
  (`deno`, or `node` via the configured option). Example: `"YouTube captions
  require a JS runtime; none found on PATH (looked for deno, node). Install deno
  or ensure node is available."`
- **R2.4** — When R1 *did* find a runtime but captions are still genuinely
  absent (e.g. a video with no captions at all), the empty result is legitimate —
  but it MUST still be reported as "no captions available for this video,"
  distinct from the missing-runtime case.

### R3 — Manage the yt-dlp dependency (stop the silent version drift)

- **R3.1** — Replace the unbounded `yt-dlp>=2025.5.1` with a **floor that
  guarantees the JS-runtime option exists** AND a **tested upper bound**, so an
  `arcus` release pins to a yt-dlp range it has actually validated against.
  Bumping the ceiling becomes a deliberate, tested step.
- **R3.2** — Document the JS-runtime runtime prerequisite for YouTube in the
  same place the `[html]` node prerequisite is documented (pyproject extras
  comments + `docs/`), so installers know a JS engine is required for YouTube
  transcripts.

### R4 — NLM fallback must not mask the primary cause

- **R4.1** — The NLM fallback (`youtube.py:151`+) MUST only engage as a genuine
  alternative path, and when it fails, the surfaced error MUST preserve the
  *primary* reason captions were unavailable (R2), not replace it with an
  NLM-auth message. Chaining/propagating the original cause is acceptable;
  silently overwriting it is not.

### R5 — Tests

- **R5.1** — Unit test: with a stubbed "no JS runtime / no captions" yt-dlp
  result, the provider returns the R2.2 precondition failure with the R2.3
  message — **not** a silent empty success and **not** an NLM-auth error.
- **R5.2** — Unit test: JS-runtime detection prefers `deno`, falls back to
  `node`, and passes the correct option to the yt-dlp adapter (assert on the
  options dict the adapter builds).
- **R5.3** — Real-network smoke (gated, like arcus's existing
  `test_fetch_metadata_smoke_real_network`): on a runner with `node`/`deno`
  available, "Me at the zoo" returns a non-empty caption transcript. This is the
  exact case Athena's AT-4 exercises.

## 5. Acceptance criteria

1. On a host with `node` (and no `deno`) and current `yt-dlp`, arcus extracts
   the "Me at the zoo" transcript successfully (R1).
2. On a host with **no** JS runtime, arcus returns the R2.2 precondition failure
   with the R2.3 actionable message — never a transcript-less success, never a
   bare NLM-auth error (R2, R4).
3. arcus's `yt-dlp` dependency is bounded to a validated range (R3).
4. R5 tests pass in CI, including the gated real-network smoke on a JS-runtime-
   equipped runner.

## 6. Out of scope

- Bundling or auto-installing a JS runtime. arcus detects and uses what's
  present and fails loudly when nothing is — provisioning a runtime is the
  installer's/environment's responsibility (and is already true for `node` via
  `arcus[html]`).
- Changes to the NLM fallback's own behavior beyond R4 (error propagation).
- Athena-side handling. Once arcus reports the loud precondition failure, Athena
  can surface it to the user; that is tracked separately on the Athena side.

## 7. References

- `packages/provider-runtime/src/arcus/provider_runtime/providers/youtube/ytdlp_adapter.py`
  — `fetch_metadata()` (L51), `parse_subtitles_from_info()` (L86),
  `fetch_captions()` (L133)
- `packages/provider-runtime/src/arcus/provider_runtime/providers/youtube/youtube.py`
  — captions gate (L111), NLM fallback (L151)
- `packages/provider-runtime/pyproject.toml` — `yt-dlp>=2025.5.1` (unbounded),
  `[html]` node-on-PATH prerequisite comment
- yt-dlp EJS / JS-runtime note: https://github.com/yt-dlp/yt-dlp/wiki/EJS
