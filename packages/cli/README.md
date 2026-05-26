# arcus (CLI)

Command-line interface for [arcus](https://github.com/polleoai/arcus) — give it
one URL or file path, get normalized Markdown + structured metadata on disk.

```bash
pipx install arcus-cli      # puts the `arcus` binary on PATH
arcus https://example.com/article --out ./out
arcus --probe <url>         # show which provider would run (no extraction)
arcus --version
```

For machine integration (subprocess + NDJSON events), see the
[integration guide](https://github.com/polleoai/arcus/blob/main/docs/integration-guide.md).
The extraction engine is the separately-published `arcus-provider-runtime` library.
