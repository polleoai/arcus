# What Arcus does

Arcus is a **content-extraction kernel**: give it one URL or one file path, and it
returns that source as normalized markdown plus structured metadata. It is the
"download + extract" layer you put *underneath* an application — it has no
opinion about what you do with the result.

> **One input in, one result out.** Arcus extracts a single source per call. It
> never crawls, recurses, aggregates, or knows anything about your storage,
> database, or domain model. If you need to process many sources, you loop at
> your layer and call Arcus once per source.

## What it handles

`arcus.provider_runtime` ships six providers. On each call, the registry
inspects the input string and the **first matching** provider wins (dispatch
order matters — more specific patterns are registered first):

| Provider | Matches | Produces | Engine |
|---|---|---|---|
| **youtube** | YouTube watch / `youtu.be` URLs | transcript + timed segments | `yt-dlp` captions, NLM fallback |
| **pdf** | `.pdf` URLs / paths | markdown text + per-page locators | `pymupdf4llm`, `pdftotext` fallback |
| **docs** | `.docx` / `.pptx` / `.xlsx` / `.epub` | markdown text + sheet/slide locators | `python-docx`, `python-pptx`, `openpyxl` |
| **text** | local `.md` / `.markdown` / `.txt` / `.text` files | markdown (near-passthrough) | reads the file directly |
| **image** | `.png` / `.jpg` / `.jpeg` / `.gif` / `.webp` / `.tiff` / `.bmp` (local + remote) | OCR'd text | Tesseract via `pytesseract` (needs the `tesseract` binary) |
| **html** | any other `http(s)` URL (catch-all) | DOM → markdown | Playwright-rendered DOM through `html2md`, incl. SPA "deep" mode and X.com tweets |

Detection is **pure string-shape** — `matches()` does no network or file IO, so
you can cheaply ask "which provider would handle this?" before committing to an
extraction.

## What you get back

Every successful extraction writes two files to the output directory you pass:

**`<slug>.md`** — YAML frontmatter + a readable body (`# <title>` then the text):

```yaml
---
source: https://example.com/article
source_id: https://example.com/article
title: The Article Title
slug: the-article-title
author: Jane Doe            # when known
kind: html
extractor_detail: {...}     # provider-specific (e.g. image URLs)
language: en                # when known
extracted_at: 2026-05-25T16:00:00Z
status: success
---

# The Article Title

...the extracted markdown body...
```

**`<slug>.json`** — the full structured payload (the source of truth for
programmatic consumers):

```json
{
  "status": "success",
  "kind": "html",
  "extractor_detail": { "images": ["https://..."] },
  "metadata": {
    "source": "https://example.com/article",
    "source_id": "https://example.com/article",
    "title": "The Article Title",
    "slug": "the-article-title",
    "author": "Jane Doe",
    "duration_ms": null,
    "posted": null,
    "language": "en"
  },
  "text": "...the extracted markdown body...",
  "segments": [ { "start_ms": 0, "end_ms": 4200, "text": "..." } ],
  "extracted_at": "2026-05-25T16:00:00Z"
}
```

`segments` is populated for time-based sources (YouTube captions); for documents
and web pages it is an empty list and `text` carries everything.

**Failures never crash.** A failed extraction writes a stub `<slug>.md` /
`<slug>.json` with `status: failed`, the `exit_code`, and an `error` string, so
no work is lost and the caller always has something to inspect.

## The contract, stated plainly

- **Single source.** One URL or file path per call; no multi-source result shape.
- **Deterministic on disk.** Re-running the same input is a cache hit (unless you
  pass `force`) — the writer checks the predicted slug + `source_id`.
- **No domain awareness.** Vault layout, topics, dedup, cross-referencing, and
  synthesis belong to the *consumer* (e.g. Athena), never to Arcus. This
  boundary is enforced by a negation test in Arcus's own suite.

## Where it runs today

Athena (the Obsidian "second brain") consumes Arcus for every URL/file it
ingests. Peitho (content-to-presentation) is the next planned consumer. Both
treat Arcus identically: construct a factory once, call it per source, read the
two output files. See the [integration guide](./integration-guide.md) for the
exact code.
