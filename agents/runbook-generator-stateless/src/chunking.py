"""Pure: split a long source into overlapping chunks on paragraph / speaker-turn / sentence boundaries."""

from __future__ import annotations

import math

SINGLE_PASS_MAX_CHARS = 24_000
CHUNK_CHARS = 20_000
OVERLAP_CHARS = 400
MAX_CHUNKS = 9  # map calls; with draft and two repairs the turn stays within 14 calls
_BOUNDARIES = ("\n\n", "\n", ". ", " ")


def chunk_size_for(length: int) -> int:
    """CHUNK_CHARS, grown just enough that a source at the size cap still fits in MAX_CHUNKS chunks."""
    needed = math.ceil((length - OVERLAP_CHARS) / MAX_CHUNKS) + OVERLAP_CHARS
    return max(CHUNK_CHARS, needed + CHUNK_CHARS // 20)


def split(source: str, size: int | None = None, overlap: int = OVERLAP_CHARS) -> tuple[str, ...]:
    """Chunks of at most `size` chars; each after the first starts `overlap` chars back, at a line start
    when one is in reach, so a speaker label is not cut off. Without an explicit size, never more than
    MAX_CHUNKS chunks."""
    if size is not None:
        return _split(source, size, overlap)
    size = chunk_size_for(len(source))
    chunks = _split(source, size, overlap)
    while len(chunks) > MAX_CHUNKS:  # boundary cuts ran short: widen until the call budget holds
        size += size // 10
        chunks = _split(source, size, overlap)
    return chunks


def _split(source: str, size: int, overlap: int) -> tuple[str, ...]:
    if len(source) <= size:
        return (source,)
    chunks: list[str] = []
    start = 0
    while start < len(source):
        end = _cut(source, start, size)
        chunks.append(source[start:end])
        if end >= len(source):
            break
        start = _next_start(source, start, end, overlap)
    return tuple(chunks)


def _cut(source: str, start: int, size: int) -> int:
    end = start + size
    if end >= len(source):
        return len(source)
    floor = start + size // 2
    for boundary in _BOUNDARIES:
        at = source.rfind(boundary, floor, end)
        if at != -1:
            return at + len(boundary)
    return end


def _next_start(source: str, start: int, end: int, overlap: int) -> int:
    back = max(end - overlap, start + 1)
    line = source.find("\n", back, end)
    return line + 1 if line != -1 and line + 1 < end else back
