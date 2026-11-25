"""PdfProvider: local + remote PDF extraction.

Dispatch:
  - matches() — local path with .pdf suffix OR http(s) URL with .pdf in path.
    Strictly suffix-based; no network probing per Provider Protocol.
    URLs without .pdf suffix fall through to HtmlProvider in the registry,
    even if they actually serve a PDF (a v1 limitation, called out in the plan).
  - predict_slug() — derives from local file stem or remote URL stem.
    Deterministic, no IO. Filename stays stable across cache-hit checks.
  - extract() — local: directly hand the path to the shared file_extract module.
    remote: HEAD-probe Content-Type for sanity (rejects HTML being served
    under a .pdf URL), urlretrieve into work_dir, extract, cleanup automatic.
"""

from __future__ import annotations

import re
import urllib.request

from arcus.provider_runtime import url_guard
from pathlib import Path
from urllib.parse import urlparse

from arcus.provider_runtime.providers._shared import markdown_text as _mt
from arcus.provider_runtime.log import now_iso
from arcus.provider_runtime.provider_interface import ExtractionContext
from arcus.provider_runtime.slug import make_slug
from arcus.provider_runtime.types import (
    EXIT_CODES,
    DetectionResult,
    ExtractionResult,
    Segment,
    SourceMetadata,
)


_HTTP_SCHEME = re.compile(r"^https?://", re.IGNORECASE)
_PDF_SUFFIX = re.compile(r"\.pdf(\?|$)", re.IGNORECASE)


def _is_http(s: str) -> bool:
    return bool(_HTTP_SCHEME.match(s))


def _has_pdf_suffix(path_or_url: str) -> bool:
    """Suffix check that ignores query strings."""
    if _is_http(path_or_url):
        return bool(_PDF_SUFFIX.search(urlparse(path_or_url).path))
    return path_or_url.lower().endswith(".pdf")


def _input_to_slug(raw_input: str) -> str:
    """Deterministic input → slug. No IO. Local: stem; remote: URL path stem."""
    if _is_http(raw_input):
        parsed = urlparse(raw_input)
        stem = ""
        path = parsed.path.rstrip("/")
        if path:
            last = path.rsplit("/", 1)[-1]
            if last.lower().endswith(".pdf"):
                last = last[:-4]
            stem = last
        slug = make_slug(stem) if stem else ""
        if slug:
            return slug
        return make_slug(parsed.netloc) or "pdf"
    stem = Path(raw_input).stem
    return make_slug(stem) or "pdf"


class PdfProvider:
    """PDF extraction for local files + remote URLs ending in .pdf."""

    kind = "pdf"

    def matches(self, raw_input: str) -> DetectionResult | None:
        if not isinstance(raw_input, str) or not raw_input:
            return None
        if not _has_pdf_suffix(raw_input):
            return None
        is_local = not _is_http(raw_input)
        return DetectionResult(
            kind="pdf",
            source_id=raw_input,
            raw=raw_input,
            metadata={"is_local": is_local, "ext": "pdf"},
        )

    def predict_slug(self, detection: DetectionResult) -> str:
        return _input_to_slug(detection.raw)

    def extract(
        self,
        detection: DetectionResult,
        context: ExtractionContext,
    ) -> ExtractionResult:
        raw = detection.raw
        slug = context.slug or _input_to_slug(raw)
        is_local = detection.metadata.get("is_local", True)

        if is_local:
            return self._extract_local(detection, raw, slug, context)
        return self._extract_remote(detection, raw, slug, context)

    # ── local ────────────────────────────────────────────────────────

    def _extract_local(
        self,
        detection: DetectionResult,
        path_str: str,
        slug: str,
        context: ExtractionContext,
    ) -> ExtractionResult:
        path = Path(path_str)
        if not path.exists():
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"file not found: {path_str}",
            )
        return self._run_extractor(detection, str(path), slug, context, source=path_str)

    # ── remote ───────────────────────────────────────────────────────

    def _extract_remote(
        self,
        detection: DetectionResult,
        url: str,
        slug: str,
        context: ExtractionContext,
    ) -> ExtractionResult:
        ct = self._head_content_type(url)
        if ct is not None and "pdf" not in ct.lower():
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["EXTRACTORS_EXHAUSTED"],
                error=f"unexpected Content-Type: {ct} (expected application/pdf)",
            )

        tmp_path = context.work_dir / f"{slug}.pdf"
        try:
            context.emit_progress("fetching")
            url_guard.download(url, str(tmp_path))
        except (OSError, urllib.error.URLError) as e:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"download failed: {e}",
            )

        return self._run_extractor(detection, str(tmp_path), slug, context, source=url)

    def _head_content_type(self, url: str) -> str | None:
        """HEAD-probe Content-Type. Returns None if HEAD fails (caller treats
        None as 'unknown — proceed under suffix assumption')."""
        try:
            req = urllib.request.Request(
                url, method="HEAD",
                headers={"User-Agent": "arcus/0.1 (PdfProvider)"},
            )
            with url_guard.urlopen(req, timeout=10) as resp:
                return (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            return None

    # ── shared extractor ─────────────────────────────────────────────

    def _run_extractor(
        self,
        detection: DetectionResult,
        filepath: str,
        slug: str,
        context: ExtractionContext,
        source: str,
    ) -> ExtractionResult:
        context.emit_progress("extracting")
        figure_opts = context.figures

        # Resource budget BEFORE any tier: page sizes only, nothing rendered.
        from arcus.provider_runtime.providers._shared import figures as _figures
        try:
            keep, pages_skipped, page_count = _figures.page_plan(filepath, context.pdf_limits)
        except Exception as e:
            # Fail closed: without a budget no tier may run on this document.
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"PDF could not measure the page budget: {type(e).__name__}: {e}",
            )
        if page_count >= 0 and not keep:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error="PDF exceeds the page budget: no page within "
                      f"{context.pdf_limits.max_page_megapixels:g} MP at "
                      f"{context.pdf_limits.render_dpi:g} dpi ({pages_skipped[:3]})",
            )
        # page_count -1: no PDF library is installed, so nothing here can
        # render pages — only pdftotext (text, no rendering) can run, and
        # Docling (which needs pypdfium2) can't be present; it is still
        # treated as NOT within budget. -2: no library can open the file, so
        # nothing can render it; the tiers report it as before.
        within_budget = (page_count >= 0 and not pages_skipped) or page_count == -2

        # Docling-primary: when the [docling] extra is installed it gives
        # layout-aware, structured Markdown. Falls back to pymupdf4llm/pdftotext.
        # Docling converts whole documents, so it only runs when every page is
        # within the budget; otherwise pymupdf4llm processes the kept pages.
        from arcus.provider_runtime.providers._shared import docling_extract
        if within_budget:
            with docling_extract.keeping_figures(figure_opts):
                docling_result = docling_extract.convert(filepath)
            if docling_result is not None:
                return docling_extract.to_extraction_result(
                    "pdf", source, slug, docling_result, figures=figure_opts)

        # Lazy import — the optional [pdf] extra may not be installed.
        try:
            from arcus.provider_runtime.providers._shared import file_extract
        except ImportError as e:
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["PROVIDER_PRIMARY_FAILED"],
                error=f"PDF extractor unavailable (install [pdf] extra): {e}",
            )

        image_dir = (str(context.work_dir / f"{slug}-pdf-images")
                     if figure_opts.enabled else None)
        with file_extract.pdf_run(image_dir=image_dir,
                                  pages=None if within_budget else (keep or None),
                                  figures=figure_opts):
            result = file_extract.extract_text(filepath, "pdf")
        text = (result or {}).get("text", "") or ""
        if not text.strip():
            return self._failure(
                detection, slug,
                exit_code=EXIT_CODES["EXTRACTORS_EXHAUSTED"],
                error="PDF extraction returned no text",
            )

        title = result.get("title", "").strip() or Path(filepath).stem
        authors = result.get("authors", "").strip() or None

        # Build per-page segments + parallel page-number locators (R5),
        # and mark whether the structured (pymupdf4llm) tier ran (R4).
        # Segment is frozen (start_ms, end_ms, text); page numbers ride in
        # extractor_detail["locators"], NOT in the time fields.
        tier = result.get("tier", "")
        pages = result.get("pages", []) or []
        # The PDF's text is untrusted (#24): the tier's output is decoded once
        # and escaped, for the body and for each page.
        text = _mt.tool_output_to_markdown(text, tier)
        segments = [Segment(start_ms=0, end_ms=0, text=_mt.tool_output_to_markdown(p["text"], tier))
                    for p in pages]
        locators = [{"segment": i, "page": p["page"]} for i, p in enumerate(pages)]

        from arcus.provider_runtime.providers._shared.figures import place_figures
        text, assets, figures, skipped = place_figures(
            text, result.get("figures") or [], slug, figure_opts)
        detail = {
            "extractor": tier or "pymupdf4llm_or_pdftotext",
            "structured": tier == "pymupdf4llm",
            "locators": locators,
        }
        if figure_opts.enabled:
            detail["figures"] = figures
            detail["figures_skipped"] = skipped
        if pages_skipped:
            detail["pages_skipped"] = pages_skipped
        if result.get("ocr"):
            detail["ocr"] = result["ocr"]
        if result.get("warnings"):
            detail["warnings"] = result["warnings"]

        return ExtractionResult(
            status="success",
            kind="pdf",
            extractor_detail=detail,
            metadata=SourceMetadata(
                source=source,
                source_id=source,
                title=title,
                slug=slug,
                author=authors,
            ),
            text=text,
            segments=segments,
            extracted_at=now_iso(),
            assets=assets,
        )

    def _failure(
        self,
        detection: DetectionResult,
        slug: str,
        *,
        exit_code: int,
        error: str,
    ) -> ExtractionResult:
        return ExtractionResult(
            status="failed",
            kind="pdf",
            extractor_detail={},
            metadata=SourceMetadata(
                source=detection.raw,
                source_id=detection.source_id,
                title="",
                slug=slug,
            ),
            text="",
            segments=[],
            extracted_at=now_iso(),
            error=error,
            exit_code=exit_code,
        )
