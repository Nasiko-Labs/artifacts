"""Pure: the masked text split into segments (paragraphs on blank lines; list items and table rows stay
with their paragraph; a paragraph over SEGMENT_MAX_CHARS splits at sentence ends), and segments packed
into chunks of at most CHUNK_CHARS for one model call each."""

from __future__ import annotations

import re
from dataclasses import dataclass

SEGMENT_MAX_CHARS = 3_000
CHUNK_CHARS = 3_000
MAX_CHUNKS = 20

_PARAGRAPH_BREAK = re.compile(r"(\n[ \t]*\n\s*)")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")
_MARKER = re.compile(r"^(\s*(?:[-*+•]|\d{1,3}[.)])\s+|\s*#{1,6}\s+|\s*>\s?)")
_LIST = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_HEADING = re.compile(r"^\s*#{1,6}\s+")
_TABLE = re.compile(r"^\s*\|")


@dataclass(frozen=True)
class Segment:
    """One unit the model translates. `sep` is the whitespace that followed it in the source; `field`
    names the envelope field it came from (None for plain text)."""

    text: str
    sep: str = ""
    field: str | None = None


def split(text: str, field: str | None = None) -> list[Segment]:
    """Segments of `text`; leading whitespace is dropped, trailing whitespace is kept as the last sep."""
    body = text.lstrip()
    pieces = _PARAGRAPH_BREAK.split(body)
    segments: list[Segment] = []
    for i in range(0, len(pieces), 2):
        paragraph, sep = pieces[i], pieces[i + 1] if i + 1 < len(pieces) else ""
        stripped = paragraph.rstrip()
        sep = paragraph[len(stripped):] + sep
        if not stripped.strip():
            if segments:
                last = segments[-1]
                segments[-1] = Segment(last.text, last.sep + paragraph + sep, field)
            continue
        segments += _sentences(stripped, sep, field)
    return segments


def _sentences(paragraph: str, sep: str, field: str | None) -> list[Segment]:
    if len(paragraph) <= SEGMENT_MAX_CHARS:
        return [Segment(paragraph, sep, field)]
    out: list[Segment] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        if current and len(current) + 1 + len(sentence) > SEGMENT_MAX_CHARS:
            out.append(Segment(current, " ", field))
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    out.append(Segment(current, sep, field))
    return out


def chunk(segments: list[Segment], size: int = CHUNK_CHARS) -> list[list[int]]:
    """Segment indexes per chunk; the size grows only if the text would need more than MAX_CHUNKS."""
    while True:
        chunks: list[list[int]] = []
        used = 0
        for i, seg in enumerate(segments):
            if chunks and used + len(seg.text) <= size:
                chunks[-1].append(i)
                used += len(seg.text)
            else:
                chunks.append([i])
                used = len(seg.text)
        if len(chunks) <= MAX_CHUNKS:
            return chunks
        size += size // 4


def join(texts: list[str], segments: list[Segment]) -> str:
    return "".join(t + s.sep for t, s in zip(texts, segments, strict=True)).rstrip()


def shape(text: str) -> tuple[int, int, int]:
    """(list items, headings, table rows) of a segment."""
    lines = (text or "").splitlines()
    return (sum(bool(_LIST.match(x)) for x in lines), sum(bool(_HEADING.match(x)) for x in lines),
            sum(bool(_TABLE.match(x)) for x in lines))


def restore_markers(source: str, translated: str) -> tuple[str, bool]:
    """Puts back list/heading/quote markers the model dropped, line by line, when the line counts match.
    Returns (text, changed)."""
    src_lines, out_lines = source.splitlines(), translated.splitlines()
    if len(src_lines) != len(out_lines):
        return translated, False
    fixed, changed = [], False
    for src, out in zip(src_lines, out_lines, strict=True):
        marker = _MARKER.match(src)
        if marker and not _MARKER.match(out):
            fixed.append(marker.group(1) + out.lstrip())
            changed = True
        else:
            fixed.append(out)
    return "\n".join(fixed), changed
