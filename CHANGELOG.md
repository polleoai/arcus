# Changelog

All notable changes to `arcus-provider-runtime` are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/); versions are bare
semver (no leading `v`) to match the release-tag convention.

## [0.3.0]

First public release.

### Added
- **HtmlProvider** — Playwright-rendered DOM → markdown via the vendored
  `html2md.mjs`, with SPA / lazy-hydration support (`deep` mode).
- **PdfProvider** — `pymupdf4llm` primary extractor with a `pdftotext`
  subprocess fallback.
- **DocsProvider** — DOCX / PPTX / XLSX / EPUB extraction (Plan A.2a).
- **YouTube provider** — `yt-dlp` caption extraction with an NLM fallback.
- MIT license, PyPI packaging (`pip install arcus-provider-runtime[html,pdf,office]`),
  and a Trusted-Publishing release workflow.

### Notes
- arcus is a pure download + extraction layer: one URL or file in, one
  normalized markdown + metadata artifact out. It has no awareness of any
  consuming application's storage, topics, or synthesis.
