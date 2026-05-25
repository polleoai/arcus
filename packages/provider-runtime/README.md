# arcus-provider-runtime

The content-extraction kernel behind [arcus](https://github.com/polleoai/arcus):
give it one URL or one file path, get back normalized markdown plus structured
metadata. No vault, no database, no project awareness — a pure download +
extraction layer you can drop into any pipeline (RAG ingest, knowledge bases,
LLM context building).

## Install

```bash
pip install "arcus-provider-runtime[html,pdf,office]"
```

Extras pull in only the heavy dependencies you need:

| Extra | Adds | For |
|---|---|---|
| `html` | `playwright` | JS-rendered pages, X.com / LinkedIn, SPA articles |
| `pdf` | `pymupdf4llm` | PDF → markdown extraction |
| `office` | `python-docx`, `python-pptx`, `openpyxl` | DOCX / PPTX / XLSX / EPUB |
| `all` | everything above | — |

The base install (YouTube transcripts via `yt-dlp`) has no extras. The HTML
provider also needs Chromium (`python -m playwright install chromium`) and
`node` on `PATH` (the vendored `html2md.mjs` converter).

## Use

```python
from arcus.provider_runtime import Factory

result = Factory().run("https://example.com/article", out_dir="./out")
# result.markdown_path  → ./out/<slug>.md   (frontmatter + readable body)
# result.metadata_path  → ./out/<slug>.json (segments, timing, provenance)
```

One `Factory.run()` entry point dispatches to the right provider by inspecting
the input. Providers live under
`arcus.provider_runtime.providers.<kind>/` and are individually registerable.

## What it deliberately does NOT do

arcus has zero awareness of any consuming app's storage, topics, or wiki. One
input in, one extracted artifact out. Vault-aware orchestration (dedup,
cross-referencing, synthesis) belongs in the consumer, not here.

## License

MIT © 2026 POLLEO.AI
