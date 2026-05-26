# Changelog

All notable changes to `arcus-provider-runtime` are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/); versions are bare
semver (no leading `v`) to match the release-tag convention.

## [Unreleased]

### Added
- **Image provider** (`kind="image"`) — OCR for `.png` / `.jpg` / `.jpeg` /
  `.gif` / `.webp` / `.tiff` / `.bmp` (local + remote) via **RapidOCR** (ONNX
  Runtime), behind the new `[image]` extra (`rapidocr-onnxruntime` +
  `rapid-table`). Pure-pip — bundles its own models, **no system binary** — and
  runs fully offline (zero network egress).
  - **Table tier:** table images are recovered as a GFM **Markdown table**
    (`structured=true`, `extractor="rapidocr+rapidtable"`) via RapidTable
    (SLANet), instead of flattening the grid into a cell list; non-tables fall
    back to plain OCR text. Recognition is isolated in `image._recognize()` so
    the backend can be swapped. `arcus --check` reports image-OCR readiness.

## [0.4.0] — 2026-05-25

arcus is now consumable from any language via a stable CLI contract — not just a
Python library. This release publishes the CLI to PyPI and firms up the
subprocess event/locator contract that out-of-process integrators (Peitho) build
against.

### Added
- **The `arcus` CLI ships in `arcus-provider-runtime`** — `pipx install arcus-provider-runtime`
  puts the `arcus` command on PATH, giving any language a first-class command-line
  surface; no Python embedding and no separate package required. (The CLI is
  pure-stdlib, so the one wheel serves both the library and the CLI.)
- **Markdown / plain-text passthrough provider** (`kind="text"`) — `.md` / `.txt`
  inputs pass through to a normalized markdown artifact without a render/extract
  round-trip.
- **Provenance locators** in `extractor_detail["locators"]` — PDF pages, xlsx
  sheets, and pptx slides each carry a structured locator so a consumer can map
  output segments back to their source position.
- **`structured` flag** in `extractor_detail` — marks output produced by a
  structured-tier extractor versus a text-only fallback.
- **Per-provider network docs** (`docs/providers-network.md`) — documents which
  providers make network calls and which run fully offline.
- **`--provider <kind>` now forces a specific provider** (was previously parsed
  but ignored): a registered provider that doesn't match the input exits `11`
  (`PROVIDER_FORCED_NO_MATCH`); an unknown kind exits `2` (`INVALID_ARGS`).

### Changed
- **NDJSON event stream** now uses a single `event` discriminator with per-stage
  progress (`fetching` / `extracting`) instead of ad-hoc event shapes.
- **Terminal `success` / `cache_hit` events** now carry `slug`, `md_path`,
  `json_path` (absolute) and `source_id`, so a subprocess caller learns the
  output paths and identity without re-deriving them.
- **CLI version** is now read from package metadata (single source of truth)
  rather than a hardcoded string.
- **Integration guide** blesses the CLI as a first-class, semver-stable surface,
  with a Node example of consuming the NDJSON stream.

### Fixed
- **Crash-safe failure stubs for remote sources** — an unhandled provider
  exception on a URL input no longer crashes the CLI (the URL was used as a
  filesystem slug); it now sanitizes the slug, writes the failure stub, and
  returns the proper exit code.
- **Correct locator ordering past 10 units** — pptx slides / xlsx sheets are now
  ordered numerically, so `slide10` no longer sorts before `slide2` and corrupts
  locator numbers.
- **No duplicate `# Heading`** in the written `<slug>.md` when the extracted body
  already opens with its own H1 (e.g. the `text` passthrough).

### Deferred
- **Image / OCR provider** — tracked in [GitHub issues](https://github.com/polleoai/arcus/issues).

## [0.3.1]

First **public** release — arcus is now open source and on PyPI.

### Added
- **MIT license.** © 2026 POLLEO.AI.
- **PyPI distribution:** `pip install "arcus-provider-runtime[html,pdf,office]"`.
  No more path/editable install — any consumer (athena included) resolves arcus
  from PyPI.
- **Release automation:** a GitHub Actions workflow builds the package, signs it
  with a sigstore build-provenance attestation, publishes to PyPI via Trusted
  Publishing (OIDC — no stored token), and cuts the GitHub Release.
- PyPI-ready package metadata (license, authors, classifiers, project URLs) and a
  focused package README.

### Notes
- No functional change to extraction since 0.3.0 — this release is about
  licensing, packaging, and distribution.

## [0.3.0]

The full provider surface (internal milestone; never published to PyPI).

### Added
- **HtmlProvider** — Playwright-rendered DOM → markdown via the vendored
  `html2md.mjs`, with SPA / lazy-hydration support (`deep` mode).
- **PdfProvider** — `pymupdf4llm` primary extractor with a `pdftotext`
  subprocess fallback.
- **DocsProvider** — DOCX / PPTX / XLSX / EPUB extraction.
- **YouTube provider** — `yt-dlp` caption extraction with an NLM fallback.

### Notes
- arcus is a pure download + extraction layer: one URL or file in, one
  normalized markdown + metadata artifact out. It has no awareness of any
  consuming application's storage, topics, or synthesis.
