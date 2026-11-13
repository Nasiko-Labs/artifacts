"""Pure: the transcript as model input lines, and its split into chunks on turn boundaries.

A transcript up to SINGLE_PASS_MAX_CHARS goes in one call. Above it, chunks of about CHUNK_CHARS,
each after the first starting with the last OVERLAP_TURNS turns of the one before, never more than
MAX_CHUNKS (the chunk size grows to fit). A single turn longer than a chunk is a chunk on its own."""

from __future__ import annotations

import math
from collections.abc import Sequence

from items import Turn

SINGLE_PASS_MAX_CHARS = 24_000
CHUNK_CHARS = 12_000
OVERLAP_TURNS = 2
MAX_CHUNKS = 10


def line(turn: Turn) -> str:
    when = f" [{turn.time}]" if turn.time else ""
    return f"{turn.id}{when} {turn.speaker}: {turn.text}"


def as_text(turns: Sequence[Turn]) -> str:
    return "\n".join(line(t) for t in turns)


def chunk_size_for(length: int) -> int:
    return max(CHUNK_CHARS, math.ceil(length / MAX_CHUNKS) + CHUNK_CHARS // 10)


def split(turns: Sequence[Turn], size: int | None = None) -> tuple[tuple[Turn, ...], ...]:
    total = len(as_text(turns))
    if size is None and total <= SINGLE_PASS_MAX_CHARS:
        return (tuple(turns),)
    if size is not None:
        return _split(turns, size)
    size = chunk_size_for(total)
    chunks = _split(turns, size)
    while len(chunks) > MAX_CHUNKS:
        size += size // 10
        chunks = _split(turns, size)
    return chunks


def _split(turns: Sequence[Turn], size: int) -> tuple[tuple[Turn, ...], ...]:
    lengths = [len(line(t)) + 1 for t in turns]
    chunks: list[tuple[Turn, ...]] = []
    start = 0
    while start < len(turns):
        end, used = start, 0
        while end < len(turns) and (end == start or used + lengths[end] <= size):
            used += lengths[end]
            end += 1
        chunks.append(tuple(turns[start:end]))
        if end >= len(turns):
            break
        start = max(end - OVERLAP_TURNS, start + 1)
    return tuple(chunks)
