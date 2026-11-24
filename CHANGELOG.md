# Changelog

All notable changes to `arcus-provider-runtime` are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/); versions are bare
semver (no leading `v`) to match the release-tag convention.

## [Unreleased]

## [0.9.0] — 2026-11-24

Web pages are converted as the browser rendered them and come back whole when
they open with a teaser; two sources with the same name keep separate outputs;
the YouTube NotebookLM fallback works with current `nlm`. HTML output changes
for JavaScript-rendered pages and for pages whose first `<article>` is a
teaser, hence the minor version.

### Fixed
- **Two sources with the same name no longer overwrite each other** — two
  different inputs that map to one slug (two `paper.pdf` files or URLs, a local
  file and a URL, two pages differing only in their query string, two videos
  with the same title) used to share `<slug>.md`, `<slug>.json` and, for PDFs,
  `<slug>.figures/`: the later extraction replaced the earlier one, and a failed
  extraction of the second source deleted the first one's figures. Each source
  now keeps its own output: the first keeps `<slug>`, later ones get
  `<slug>--<hash>`, and re-running a source finds its own files again. The
  writer refuses to replace another source's output rather than overwrite it.
  Applies to every provider (pdf, docs, image, text, html, youtube). Run one
  extraction at a time per output directory: names are claimed when a run
  starts, so two concurrent runs into one directory can still pick the same
  name (the later write is then normally refused, exit 10, rather than
  overwriting).
  **For provider authors and library callers:** `ExtractionContext.slug` is the
  output name the run claimed — use it (fall back to your own slug when it is
  `None`); a provider that writes a different, already-taken name fails with
  exit 10 instead of overwriting. `write_success` and `write_failure_stub` now
  raise `SlugTakenError` rather than replace another source's files; read the
  final `slug` / `md_path` from the run's events, not from your own guess.
- **YouTube NotebookLM fallback works with current `nlm` and fails loudly
  without it** — the fallback's `nlm` CLI is now a declared dependency,
  `notebooklm-mcp-cli>=0.9.4,<1`, checked (`nlm --version`) before use. A
  missing or out-of-range `nlm` fails the extraction with exit code 20 and an
  install hint instead of an unhandled error. Against the declared version
  the fallback misread notebook and source IDs and the transcript (piped `nlm`
  output is JSON) and could not delete its notebook (`--confirm` was
  missing): it now asks for JSON, confirms the delete, never waits on a
  prompt, and reports an ingest timeout as a fallback failure. `arcus --check`
  shows the `nlm` version against the requirement.
- **HTML pages are converted as the browser rendered them** — the html provider
  rendered each page in Chromium, then threw that DOM away and converted a
  second, static download of the page, so content added by JavaScript never
  reached the markdown. The rendered DOM, including content web components keep
  in shadow DOM (MDN's code examples, for one), is now what gets converted, with
  links resolved against the page's final URL. The page is no longer downloaded
  a second time. Output changes for pages that render content client-side.
- **HTML articles whose first `<article>` is a teaser come back whole** — on
  component-CMS pages (Adobe AEM "insights" pages, for one) the page's only
  `<article>` is a short hero/teaser and the body sits in containers after it;
  on blog index pages the first `<article>` is one post of many. The html
  provider kept just that element (a ~2,200-word article came back as 363
  characters). When the chosen element holds under 30% of the page's visible
  non-navigation text, on a page with at least 1,000 such characters, the whole
  page is converted instead; the plain-text fallback (used when conversion
  fails) applies the same rule. A `<main>` or `<article>` is no
  longer cut short at a nested `</article>`, and page chrome (`<nav>`,
  `<header>`, `<footer>`, `<aside>`, ARIA navigation/banner/contentinfo regions
  and dialogs) is removed whole even when nested. Output changes for pages with
  navigation sidebars or boxes (now dropped) and for pages with a teaser root.

## [0.8.2] — 2026-11-22

Long PDFs come back whole, figure captions are matched more reliably, and the
PDF extras stay on a supported pymupdf4llm.

### Fixed
- **Long PDFs are no longer cut at 50,000 characters** — on the `pymupdf4llm`
  and `pdftotext` tiers, PDF text was silently shortened to 50,000 characters,
  dropping roughly half of a typical 30-page paper (figures past the cut ended
  up at the end of the text). PDF text now comes back whole on every tier; its
  size is bounded by the page budget (`PdfLimits.max_pages`).
- **Figure captions further from their image are found** — when axis labels,
  legends or panel labels sit between a figure and its `Figure N:` line, the
  caption is still matched (the search now looks a few lines further and stops
  at a section heading). Fixes panels being labelled with the previous
  figure's caption.
- **pymupdf4llm 2.x** — the `[pdf]` and `[all]` extras now require
  `pymupdf4llm<2`, the version arcus is tested against. If 2.x is installed
  anyway, its picture-text markers are understood and the result carries a
  `PYMUPDF4LLM_UNSUPPORTED` warning in `extractor_detail["warnings"]`.
- **URL guard: malformed requests** — the guard's local proxy now answers any
  malformed request with `400 Bad Request` and closes the connection, instead of
  letting the request break its handler. It still fails closed.

### Added
- **`ExtractionLimits(max_text_chars=50_000)`** — the text cap on docx, xlsx,
  pptx and epub is now an explicit, documented option
  (`factory.run(..., extraction_limits=...)`; `None` turns it off). When it cuts,
  `extractor_detail` carries `truncated: true`,
  `truncated_reason: "max_text_chars"` and `original_chars`. Nothing is
  shortened silently.

## [0.8.1] — 2026-11-22

X posts work again without a browser session.

### Fixed
- **X (Twitter) posts no longer come back empty** — X now refuses headless
  browsers without a signed-in session, so every X capture failed with "no
  text". Without a session, the `html` provider now reads the post from X's
  public embed (syndication) endpoint: short posts come back complete (links
  expanded, photos in `extractor_detail["images"]`, quoted post included,
  author and date filled in); unavailable posts fail with a clear
  "x post unavailable" error. The URL guard applies to that request.

### Added
- **Truncation flag for long X posts** — the embed endpoint only returns a
  preview of long posts; those come back with
  `extractor_detail["truncated"] = true`, `"truncated_reason": "no_session"`
  and `"truncation_signal"`, so a consumer can complete them another way.
  Short posts report `truncated: false`.

### Docs
- Known limit: on the `pymupdf4llm` PDF tier, images that tier never outputs
  can't appear in `figures_skipped`.

## [0.8.0] — 2026-11-22

PDF figures are kept: extraction now saves each real figure as a PNG and
references it in the markdown where it appears, instead of dropping every image.

### Added
- **PDF figures** (`pdf` provider, both the Docling and the `pymupdf4llm` tier) —
  each figure is written to `<slug>.figures/figure-NN-pP.png` next to the
  markdown, referenced in place (`![caption](…)`), and listed in
  `extractor_detail["figures"]` (`file`, `page`, `caption`, `width`, `height`,
  `bytes`). Tiny decorative images are skipped. Bounds per document via
  `FigureOptions` (`max_figures`, `max_total_bytes`, `min_side_pt`, `enabled`) on
  `factory.run(figures=…)` / `ExtractionContext`, or `--max-figures N` on the
  CLI; over a bound the document still extracts and everything skipped is listed
  in `extractor_detail["figures_skipped"]` with a reason.
- **PDF resource budget** — `PdfLimits` (`max_pages`, `max_page_megapixels`,
  `render_dpi`; `factory.run(pdf_limits=…)`) is checked from page sizes before
  any extraction tier runs, so an oversized page is never rendered. Skipped
  pages are listed in `extractor_detail["pages_skipped"]`; a PDF with no page
  inside the budget fails with a clear error. Embedded images whose header
  claims more than `FigureOptions.max_image_megapixels` are never decoded.
- **`ExtractionResult.assets`** — the figure files (`Asset(path, data)`) for
  callers that use a provider directly; the writer stores them and replaces a
  slug's figures on re-extraction.
- **Clear error when the browser is missing** — HTML captures fail with a
  message that says to run `playwright install chromium`, carried in the
  result's `error`.

### Fixed
- **PDFs with images no longer fall back to plain text** — the `pymupdf4llm`
  tier crashed in its OCR step with an incompatible OCR backend installed and
  silently dropped to `pdftotext`; for that known failure only it now retries
  without OCR and records it in `extractor_detail["ocr"]`. Other errors are not
  retried. A tier that returns no text still falls back to `pdftotext`.

## [0.7.2] — 2026-11-22

Hardening follow-up to 0.7.1's URL guard: proxy settings from the environment
or the operating system can no longer change how a guarded fetch is routed.

### Fixed
- **Guarded fetches ignore proxy settings from the environment or the
  operating system** — with a URL guard set, every path keeps to the guard:
  arcus's own downloads connect directly under the guard, yt-dlp stays on the
  guard proxy whichever HTTP backend it uses, and the headless browser starts
  without proxy settings. This applies only to the thread running the guarded
  call: nothing process-wide is changed, and unguarded code (including other
  threads) keeps its normal proxy behaviour. Without a guard, nothing changes.

## [0.7.1] — 2026-11-22

Security hardening for consumers that pass untrusted URLs: arcus can now
validate redirects and sub-requests against a caller policy, not just the
first URL it is handed.

### Added
- **Caller URL guard** — `set_url_guard(policy)` / `using_url_guard(policy)`
  install a callable `policy(url) -> None` (raise to refuse) that arcus applies
  to every URL it fetches: the input, every HTTP redirect hop of its downloads
  and probes, every headless-browser navigation and sub-request (frames,
  scripts, images, popups, WebSockets), and every yt-dlp request. Optional
  `urlopen=` lets a caller supply its own pinned-address opener for the
  plain-HTTP paths. Opt-in: with no guard set, behavior is unchanged.
- **`UrlRefusedError`** and exit code **`URL_REFUSED` (12)** — a refused page is
  always a reported failure (the `failed` event carries `refused_url`), never a
  silent empty result. Refused sub-resources are blocked and the page still
  extracts.

- **Guard contract and scoping** — a guard returns `None` to allow and raises to
  refuse; any other return value counts as a refusal. `using_url_guard` scopes
  to the current thread / task, and each fetch keeps the guard it started with.

### Changed
- With a guard set, the headless browser and yt-dlp run through a short-lived
  local guard proxy (127.0.0.1, one per fetch), with browser features that could
  bypass it switched off and yt-dlp limited to its native downloader. If that
  proxy can't run, the fetch fails rather than running unguarded. Without a
  guard, nothing changes.
- The optional `html` extra now requires `playwright>=1.48`.

### Notes
- Pair the guard with network-level egress sandboxing for network-level
  guarantees.

## [0.7.0] — 2026-07-01

The `html` provider gains reveal.js slide-deck awareness — talk decks that used
to come back as an empty body or markdown still leaking raw HTML entities now
extract as clean, per-slide text, ported from Athena's stopgap fallback into
arcus where single-URL extraction belongs.

### Added
- **reveal.js slide-deck extraction (`html` provider)** — detects a reveal.js
  deck (`class="reveal"` element with >= 3 `<section>`s) and, gated on the
  existing mis-extraction fingerprint (empty body or HTML-entity smell:
  leftover `&middot;` / `&amp;` / `&#NN;`), re-extracts it via a depth-aware
  leaf-`<section>` `HTMLParser` walk instead of the generic html2md path. The
  walk is depth-aware specifically to preserve **nested vertical slides**
  (`<section>` inside `<section>`) that a flat regex would drop or merge.
  Each slide becomes a discrete segment with a slide-number locator
  (`{"segment": i, "slide": i+1}`), mirroring how the `docs` provider already
  emits per-sheet/per-slide segments for xlsx/pptx. Clean article captures pay
  no extra cost — the deck re-fetch only fires on the mis-extraction fingerprint.
  Ports and closes out Athena's `bin/lib/slide_deck.py` stopgap: once Athena
  adopts this release, it drops that fallback module along with the
  `arcus_html.py` wiring and entity-smell gate. Refs #8, PR #9.

## [0.6.0] — 2026-05-26

The `[office]` extra becomes a real pure-pip structured engine for docx/xlsx/pptx
— structure-preserving office extraction now works with no system `pandoc` binary
and no heavy Docling models, while Docling stays primary when installed.

### Added
- **Pure-pip structured office tier** — the `[office]` extra (`openpyxl` /
  `python-pptx` / `python-docx`) is now an *active* structured engine for
  `docx` / `xlsx` / `pptx`, not just declared deps: xlsx sheets → GFM tables,
  pptx slides → per-slide sections, docx headings/tables → Markdown. It slots
  into the fallback ladder as **Docling → `[office]` libs → pandoc → stdlib zip**,
  so structured office extraction no longer requires a system `pandoc` binary or
  the heavy Docling models. Per-sheet/slide locators come from this tier when it
  runs. (Docling remains primary when `[docling]` is installed.)

### Fixed
- **Truthful `extractor_detail.extractor`** — `docs` now reports the actual tier
  that ran (`openpyxl` / `python-pptx` / `python-docx` / `pandoc` / `zipfile`)
  instead of static, partly-fictional labels (e.g. xlsx previously claimed
  `"openpyxl-or-pandoc"` while no office lib was ever imported).
- **`structured` flag now covers the office-lib tiers** (was `pandoc`-only), so
  structured xlsx/pptx/docx output is correctly marked.

## [0.5.0] — 2026-05-26

Structure-aware extraction for pdf / docs / images. Installing the optional
`[docling]` extra makes **Docling** the primary engine for those formats
(layout + table-structure aware, clean Markdown, per-page locators), and a new
**image** provider adds OCR + table recognition. Both are opt-in extras, so the
base install stays lightweight; existing `kind` values and the output contract
are unchanged.

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
- **Docling backend** (new `[docling]` extra) — high-fidelity, structure-aware
  extraction (layout + TableFormer tables → clean Markdown) for **pdf / docs /
  image**. When the extra is installed, Docling is the **primary** engine for
  those three providers; they **fall back** to the lightweight extractors
  (pymupdf4llm / pandoc / rapidocr+rapidtable) when it is absent or a conversion
  fails — so the base install stays light and fast. `kind` values are unchanged;
  `html` (Playwright) and `youtube` (yt-dlp) are unaffected. Runs fully offline
  (CPU-pinned to avoid an Apple-Silicon MPS bug). Emits `structured=true` Markdown
  plus per-page `locators` (`{"segment": i, "page": n}`) and matching segments
  derived from Docling page provenance — the same shape the lightweight fallback
  emits, so consumers map output back to source pages regardless of engine.
- **`arcus --check` now reports Docling readiness** alongside image-OCR, so users
  can see whether the high-fidelity pdf/docs/image engine is installed.

### Fixed
- **No duplicate title heading** — the writer no longer prepends `# {title}` when
  the extracted body already opens with a heading of *any* level (was H1-only;
  Docling bodies open with `##`).
- **Docling `<!-- image -->` placeholders stripped** from the output body, with the
  leftover blank runs collapsed.
- **Title derivation skips HTML-comment placeholder lines**, so a Docling image
  comment no longer becomes the document title.
- **Colspan-aware OCR tables** — a cell spanning N columns now occupies its text
  plus N−1 blanks, so spanned cells land in the correct columns.

### Internal
- Lint debt cleared (ruff clean): the vendored `_athena_fetch_page.py` is exempted
  via `per-file-ignores`; genuine issues fixed in first-party files.

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
