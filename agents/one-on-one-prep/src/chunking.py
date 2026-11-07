"""Pure: cut a long part into chunks on paragraph or line boundaries. Never truncates silently: what was
not read is counted for the coverage note."""

from __future__ import annotations

from dataclasses import dataclass

SINGLE_PASS_CHARS = 24_000
CHUNK_CHARS = 12_000
MAX_CHUNKS_PER_PART = 3


@dataclass(frozen=True)
class Chunks:
    parts: tuple[str, ...]
    skipped_chars: int = 0


def _pieces(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        while len(line) > CHUNK_CHARS:
            out.append(line[:CHUNK_CHARS])
            line = line[CHUNK_CHARS:]
        out.append(line)
    return out


def chunk(text: str) -> Chunks:
    chunks: list[str] = []
    current = ""
    for piece in _pieces(text or ""):
        if current and len(current) + len(piece) > CHUNK_CHARS:
            chunks.append(current)
            current = ""
        current += piece
    if current.strip():
        chunks.append(current)
    kept = chunks[:MAX_CHUNKS_PER_PART]
    return Chunks(tuple(kept), sum(len(c) for c in chunks[MAX_CHUNKS_PER_PART:]))
