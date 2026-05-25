# Changelog

All notable changes to `arcus-provider-runtime` are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/); versions are bare
semver (no leading `v`) to match the release-tag convention.

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
