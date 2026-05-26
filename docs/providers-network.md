# Per-provider network behavior

Arcus runs untrusted user input. This table documents exactly what egress each
provider requires, so a consumer (e.g. Peitho) can sandbox extraction to "fetch
the source, nothing else."

| Provider | Network egress | Notes |
|---|---|---|
| `text` | **None.** | Local files only; pure passthrough. Remote `http(s)` URLs are routed to other providers, never fetched here. |
| `pdf` (local) | **None.** | Reads the local file; `pdftotext`/pymupdf4llm are local. |
| `pdf` (remote) | HTTP(S) HEAD + GET to the source URL only. | One HEAD (Content-Type probe, rejects HTML served under a `.pdf` URL) + one GET (`urlretrieve` download). |
| `docs` (local) | **None.** | Local file; pandoc/stdlib are local. |
| `docs` (remote) | HTTP(S) GET to the source URL only. | Single `urlretrieve` download — **no** HEAD probe (unlike `pdf`). |
| `image` (local) | **None.** | Reads the local file; OCR + table recognition run fully offline via RapidOCR + RapidTable (bundled ONNX models, pure-pip — no system binary). |
| `image` (remote) | HTTP(S) GET to the source URL only. | Single `urlretrieve` download, then local OCR. |
| `html` | HTTP(S) to the source URL **and its sub-resources** (Playwright renders the page). | A real headless Chromium loads scripts/styles/images from whatever the page references — egress is **not** limited to the origin host. Sandbox accordingly. |
| `youtube` | yt-dlp egress to YouTube + (fallback) `nlm` egress to NotebookLM. | Captions path: YouTube only (yt-dlp Python API). NLM fallback uploads the URL to NotebookLM (Google) via the `nlm` CLI and needs `nlm login` auth. |

**Sandbox guidance:** local `text`/`pdf`/`docs`/`image` need zero egress (image
OCR is fully local). Remote `pdf`/`docs`/`image` need egress to the single source
host. `html` needs broad web egress (it drives a browser). `youtube` needs YouTube
and possibly NotebookLM.
