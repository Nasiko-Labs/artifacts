"""Pure: split a long log into chunks of whole lines, each after the first starting OVERLAP_LINES back, so
an event that straddles a cut is seen whole at least once. Never more than MAX_CHUNKS chunks."""

from __future__ import annotations

import math
from collections.abc import Sequence

from times import Line

SINGLE_PASS_MAX_CHARS = 24_000
CHUNK_CHARS = 12_000
OVERLAP_LINES = 10
MAX_CHUNKS = 10


def text_of(lines: Sequence[Line]) -> str:
    return "\n".join(line.raw for line in lines)


def split(lines: Sequence[Line], size: int | None = None) -> tuple[tuple[Line, ...], ...]:
    total = len(text_of(lines))
    if total <= SINGLE_PASS_MAX_CHARS and size is None:
        return (tuple(lines),)
    size = size or max(CHUNK_CHARS, math.ceil(total / MAX_CHUNKS) + CHUNK_CHARS // 10)
    chunks = _split(lines, size)
    while len(chunks) > MAX_CHUNKS:
        size += size // 10
        chunks = _split(lines, size)
    return chunks


def _split(lines: Sequence[Line], size: int) -> tuple[tuple[Line, ...], ...]:
    chunks: list[tuple[Line, ...]] = []
    start = 0
    while start < len(lines):
        end, used = start, 0
        while end < len(lines) and (end == start or used + len(lines[end].raw) + 1 <= size):
            used += len(lines[end].raw) + 1
            end += 1
        chunks.append(tuple(lines[start:end]))
        if end >= len(lines):
            break
        start = max(start + 1, end - OVERLAP_LINES)
    return tuple(chunks)
