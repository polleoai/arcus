"""WebVTT parser → Segment list, plus paragraph reflow for readable .md body."""

from __future__ import annotations

import re

from arcus.provider_runtime.types import Segment


_TIMESTAMP = re.compile(r"^(\d{1,2}:)?(\d{1,2}):(\d{2})\.(\d{3})$")
_CUE_LINE = re.compile(
    r"^((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{3})\s+-->\s+"
    r"((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{3})(?:\s+.*)?$"
)
_HTML_TAG = re.compile(r"<[^>]+>")
_PARAGRAPH_GAP_MS = 1500


def _ts_to_ms(ts: str) -> int:
    m = _TIMESTAMP.match(ts)
    if not m:
        raise ValueError(f"Invalid timestamp: {ts}")
    hours = int(m.group(1)[:-1]) if m.group(1) else 0
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    millis = int(m.group(4))
    return (hours * 3600 + minutes * 60 + seconds) * 1000 + millis


def _strip_formatting(text: str) -> str:
    text = _HTML_TAG.sub("", text)
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )


def parse_vtt(content: str) -> list[Segment]:
    """Parse a WebVTT string into a list of Segment objects."""
    lines = content.splitlines()
    segments: list[Segment] = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        m = _CUE_LINE.match(line)
        if not m:
            i += 1
            continue

        start_ms = _ts_to_ms(m.group(1))
        end_ms = _ts_to_ms(m.group(2))
        i += 1

        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip() != "":
            text_lines.append(_strip_formatting(lines[i].strip()))
            i += 1

        text = " ".join(text_lines).strip()
        if text:
            segments.append(Segment(start_ms=start_ms, end_ms=end_ms, text=text))

    return _dedupe_rolling(segments)


def _dedupe_rolling(segments: list[Segment]) -> list[Segment]:
    """Merge adjacent segments with identical text (YouTube rolling-caption artifact)."""
    if not segments:
        return segments
    out: list[Segment] = []
    for s in segments:
        if out and out[-1].text == s.text:
            # Replace the last segment with one whose end_ms extends.
            prev = out[-1]
            out[-1] = Segment(start_ms=prev.start_ms, end_ms=s.end_ms, text=prev.text)
            continue
        out.append(s)
    return out


def build_paragraphs(segments: list[Segment]) -> list[str]:
    """Group segments into paragraphs broken by gaps > 1.5s."""
    if not segments:
        return []
    paragraphs: list[str] = []
    current: list[str] = [segments[0].text]
    last_end = segments[0].end_ms

    for s in segments[1:]:
        if s.start_ms - last_end > _PARAGRAPH_GAP_MS:
            paragraphs.append(" ".join(current))
            current = [s.text]
        else:
            current.append(s.text)
        last_end = s.end_ms

    paragraphs.append(" ".join(current))
    return paragraphs
