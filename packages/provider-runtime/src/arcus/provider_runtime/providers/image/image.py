"""ImageProvider: OCR an image into normalized text via RapidOCR.

Local images are read directly; remote image URLs are downloaded into the
work_dir then OCR'd (same local-or-remote shape as the PDF/Docs providers).

OCR backend: RapidOCR (ONNX Runtime) via the `rapidocr-onnxruntime` package in
the optional `[image]` extra. It is **pure-pip** — it bundles its own models and
runtime, so there is no system binary to install — and runs fully locally (zero
network egress), so this provider is sandbox-friendly.

Table tier: when the image is a table, plain OCR would flatten the grid into a
linear list of cells. So after OCR we also run RapidTable (SLANet, also ONNX /
pure-pip, in the `[image]` extra) to recover the row/column structure and emit a
GFM **Markdown table** (with `structured=True`). When no real grid is detected we
fall back to plain OCR text (`structured=False`). Recognition is isolated in
`_recognize()` so the backend can be swapped. `locators` is empty for now
(per-cell/line boxes are available from both engines and can be surfaced later).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from arcus.provider_runtime.log import now_iso
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.slug import make_slug
from arcus.provider_runtime.types import (
    EXIT_CODES,
    DetectionResult,
    ExtractionResult,
    SourceMetadata,
)


_HTTP_SCHEME = re.compile(r"^https?://", re.IGNORECASE)
_SUPPORTED_EXTS = ("png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif")
_EXT_PATTERN = re.compile(rf"\.({'|'.join(_SUPPORTED_EXTS)})(\?|$)", re.IGNORECASE)
_HEADING = re.compile(r"^#+\s*")


class OcrUnavailableError(RuntimeError):
    """Raised when the OCR backend (the `[image]` extra) is not installed."""


# The ONNX engines load models on construction (~seconds), so reuse one of each
# across calls. Module-level + lazy so import is cheap and the cost is paid once.
_ocr_engine = None
_table_engine = None


class _TableHTMLParser(HTMLParser):
    """Collect rows of (text, colspan) from a simple `<table>` (no nesting)."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[tuple[str, int]]] = []
        self._row: list[tuple[str, int]] | None = None
        self._cell: list[str] | None = None
        self._colspan = 1

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []
            self._colspan = int(dict(attrs).get("colspan", "1") or 1)

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._row is not None and self._cell is not None:
            self._row.append((" ".join("".join(self._cell).split()), self._colspan))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _html_table_to_markdown(html: str) -> str | None:
    """Convert RapidTable's `<table>` HTML to a GFM Markdown table.

    Returns None when the structure isn't a real grid (< 2 rows or < 2 columns) —
    the signal that the image wasn't a table and we should fall back to plain text.
    Full-width (`colspan == ncols`) rows become caption lines before/after the table.
    """
    parser = _TableHTMLParser()
    parser.feed(html or "")
    rows = parser.rows
    if len(rows) < 2:
        return None
    ncols = max((sum(c for _, c in r) for r in rows), default=0)
    if ncols < 2:
        return None

    grid: list[list[str]] = []
    captions_before: list[str] = []
    captions_after: list[str] = []
    seen_grid = False
    for r in rows:
        if len(r) == 1 and r[0][1] >= ncols:  # full-width caption row
            (captions_after if seen_grid else captions_before).append(r[0][0])
            continue
        cells = [t for t, _ in r] + [""] * ncols
        grid.append(cells[:ncols])
        seen_grid = True
    if len(grid) < 2:
        return None

    out = [f"**{c}**\n" for c in captions_before if c]
    out.append("| " + " | ".join(grid[0]) + " |")
    out.append("| " + " | ".join("---" for _ in range(ncols)) + " |")
    for row in grid[1:]:
        out.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
    out.extend(f"\n{c}" for c in captions_after if c)
    return "\n".join(out)


def _recognize(filepath: str) -> tuple[str, bool, str]:
    """OCR an image; return (content, structured, extractor).

    Runs RapidOCR for text. If RapidTable recovers a real grid, `content` is a GFM
    Markdown table and `structured` is True; otherwise `content` is plain OCR text
    and `structured` is False. Raises OcrUnavailableError when the `[image]` extra
    (rapidocr-onnxruntime) is not installed. Pure-pip — no system binary required.
    """
    global _ocr_engine
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        raise OcrUnavailableError(
            "image OCR needs the [image] extra: "
            "pip install 'arcus-provider-runtime[image]'"
        ) from e
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    ocr_res, _elapse = _ocr_engine(filepath)
    if not ocr_res:
        return "", False, "rapidocr"

    table_md = _try_table(filepath, ocr_res)
    if table_md:
        return table_md, True, "rapidocr+rapidtable"
    return "\n".join(line[1] for line in ocr_res), False, "rapidocr"


def _try_table(filepath: str, ocr_res) -> str | None:
    """Best-effort table-structure recovery via RapidTable → Markdown, else None.

    Table recognition is advisory: any failure (extra not installed, model error,
    not actually a table) returns None so the caller falls back to plain OCR text.
    """
    global _table_engine
    try:
        import numpy as np
        from rapid_table import RapidTable
    except ImportError:
        return None
    try:
        if _table_engine is None:
            _table_engine = RapidTable()
        boxes = np.array([it[0] for it in ocr_res], dtype=np.float32)
        texts = tuple(it[1] for it in ocr_res)
        scores = tuple(float(it[2]) for it in ocr_res)
        out = _table_engine(filepath, [(boxes, texts, scores)])
        htmls = getattr(out, "pred_htmls", None) or []
        return _html_table_to_markdown(htmls[0]) if htmls else None
    except Exception:
        return None


def _is_http(s: str) -> bool:
    return bool(_HTTP_SCHEME.match(s))


def _detect_ext(path_or_url: str) -> str | None:
    path = urlparse(path_or_url).path if _is_http(path_or_url) else path_or_url
    m = _EXT_PATTERN.search(path)
    return m.group(1).lower() if m else None


def _input_to_slug(raw_input: str) -> str:
    if _is_http(raw_input):
        parsed = urlparse(raw_input)
        last = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        stem = Path(last).stem
        return make_slug(stem) or make_slug(parsed.netloc) or "image"
    return make_slug(Path(raw_input).stem) or "image"


def _title_from(text: str, fallback: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("|"):  # skip blank lines and table rows
            continue
        # strip leading markdown heading/emphasis and surrounding `**` (table caption)
        cleaned = _HEADING.sub("", line).strip().strip("*").strip()
        return cleaned[:80] or fallback
    return fallback


class ImageProvider:
    """OCR-based extraction for local + remote image files."""

    kind = "image"

    def matches(self, raw_input: str) -> DetectionResult | None:
        if not isinstance(raw_input, str) or not raw_input:
            return None
        ext = _detect_ext(raw_input)
        if ext is None:
            return None
        return DetectionResult(
            kind="image",
            source_id=raw_input,
            raw=raw_input,
            metadata={"is_local": not _is_http(raw_input), "ext": ext},
        )

    def predict_slug(self, detection: DetectionResult) -> str:
        return _input_to_slug(detection.raw)

    def extract(
        self,
        detection: DetectionResult,
        context: ExtractionContext,
    ) -> ExtractionResult:
        raw = detection.raw
        slug = _input_to_slug(raw)
        if detection.metadata.get("is_local", True):
            path = Path(raw)
            if not path.exists():
                return self._failure(
                    detection, slug, EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                    f"file not found: {raw}",
                )
            return self._run_ocr(detection, str(path), slug, source=raw, context=context)

        ext = detection.metadata.get("ext", "png")
        tmp_path = context.work_dir / f"{slug}.{ext}"
        context.emit_progress("fetching")
        try:
            urllib.request.urlretrieve(raw, str(tmp_path))
        except (OSError, urllib.error.URLError) as e:
            return self._failure(
                detection, slug, EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                f"download failed: {e}",
            )
        return self._run_ocr(detection, str(tmp_path), slug, source=raw, context=context)

    def _run_ocr(
        self,
        detection: DetectionResult,
        filepath: str,
        slug: str,
        *,
        source: str,
        context: ExtractionContext,
    ) -> ExtractionResult:
        context.emit_progress("extracting")
        try:
            content, structured, extractor = _recognize(filepath)
        except OcrUnavailableError as e:
            return self._failure(
                detection, slug, EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                f"image OCR unavailable: {e}",
            )
        except Exception as e:  # malformed image, decode error, etc.
            return self._failure(
                detection, slug, EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                f"OCR failed: {e}",
            )

        content = (content or "").strip()
        if not content:
            return self._failure(
                detection, slug, EXIT_CODES["EXTRACTORS_EXHAUSTED"],
                "OCR produced no text (image may have no legible text)",
            )

        return ExtractionResult(
            status="success",
            kind="image",
            extractor_detail={"extractor": extractor, "structured": structured, "locators": []},
            metadata=SourceMetadata(
                source=source,
                source_id=source,
                title=_title_from(content, Path(filepath).stem),
                slug=slug,
            ),
            text=content,
            segments=[],
            extracted_at=now_iso(),
        )

    def _failure(self, detection, slug, exit_code, error) -> ExtractionResult:
        return ExtractionResult(
            status="failed",
            kind="image",
            extractor_detail={},
            metadata=SourceMetadata(
                source=detection.raw, source_id=detection.source_id, title="", slug=slug,
            ),
            text="",
            segments=[],
            extracted_at=now_iso(),
            error=error,
            exit_code=exit_code,
        )
