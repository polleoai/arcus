# arcus

**Content extraction kernel.** Turn a URL or path into a normalized transcript on disk. Pluggable providers per input kind (YouTube, HTML, PDF, Athena topic). Mirrors gryphon's `provider-runtime` pattern in Python.

> **Status:** design phase. Implementation begins with Plan A.0 — see `docs/plans/`.

## What's here

```
docs/
  specs/
    2026-05-17-arcus-provider-runtime-design.md     ← top-level architecture
  plans/
    2026-05-17-arcus-plan-a0-provider-runtime.md    ← ready to execute (~16 tasks, full TDD)
    2026-05-17-arcus-plan-a1-html-pdf-athena-providers.md  ← outline; expand after A.0
    2026-05-17-arcus-plan-a2-athena-migration.md    ← outline; cross-repo work in athena
    2026-05-17-plan-a-arcus.md                      ← Node draft, superseded (algorithmic reference)
```

## Quick read

- **Why arcus exists:** athena's `bin/lib/fetch-page.py` / `file_extract.py` / etc. are already content extractors — they're just trapped inside athena. Arcus modularizes them into a reusable provider-runtime + adds YouTube transcript extraction (the gap athena doesn't fill). Consumers (athena, peitho, future projects) share one canonical implementation.
- **Plan order:** A.0 (provider-runtime + YouTube) → A.1 (HTML, PDF, Athena-Topic providers, modularized from athena) → A.2 (athena migration: deletes its copies, imports arcus) → peitho's multi-source consumer plan (Plan B, lives in the peitho repo).
- **Language:** Python 3.11+. uv-managed monorepo. yt-dlp Python API for in-process YouTube extraction; `nlm` CLI subprocess for NotebookLM ASR fallback.

## Next step

Plan A.0 is shippable on its own. Start with Task 1 (`gh repo create` + scaffold) inside this directory.
