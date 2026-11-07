"""Pure: plan the map calls. A source over CHUNK_CHARS is cut into overlapping chunks on paragraph / line /
sentence boundaries; when there are more pieces than MAX_MAP_CALLS, small pieces are packed into shared
units of at most CHUNK_CHARS (source markers kept); whatever still does not fit is not read."""

from __future__ import annotations

from dataclasses import dataclass

from model import Source

CHUNK_CHARS = 12_000
OVERLAP_CHARS = 400
MAX_MAP_CALLS = 24
_BOUNDARIES = ("\n\n", "\n", ". ", " ")


@dataclass(frozen=True)
class Segment:
    source_id: str
    label: str
    text: str
    part: int = 1
    parts: int = 1


@dataclass(frozen=True)
class Unit:
    segments: tuple[Segment, ...]

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s.source_id for s in self.segments))

    @property
    def size(self) -> int:
        return sum(len(s.text) for s in self.segments)


def plan_units(sources: tuple[Source, ...], max_calls: int = MAX_MAP_CALLS) -> tuple[tuple[Unit, ...], tuple[Unit, ...]]:
    """(units to read, units over the call cap)."""
    segments = [seg for s in sources for seg in segments_of(s)]
    if len(segments) <= max_calls:
        return tuple(Unit((seg,)) for seg in segments), ()
    units = _pack(segments)
    return tuple(units[:max_calls]), tuple(units[max_calls:])


def segments_of(source: Source) -> tuple[Segment, ...]:
    chunks = split(source.text)
    return tuple(Segment(source.id, source.label, c, n, len(chunks)) for n, c in enumerate(chunks, start=1))


def _pack(segments: list[Segment]) -> list[Unit]:
    units: list[list[Segment]] = []
    for seg in segments:
        if units and sum(len(s.text) for s in units[-1]) + len(seg.text) <= CHUNK_CHARS:
            units[-1].append(seg)
        else:
            units.append([seg])
    return [Unit(tuple(u)) for u in units]


def split(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> tuple[str, ...]:
    """Chunks of at most `size` chars; each after the first starts `overlap` chars back."""
    if len(text) <= size:
        return (text,)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = _cut(text, start, size)
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return tuple(chunks)


def _cut(text: str, start: int, size: int) -> int:
    end = start + size
    if end >= len(text):
        return len(text)
    floor = start + size // 2
    for boundary in _BOUNDARIES:
        at = text.rfind(boundary, floor, end)
        if at != -1:
            return at + len(boundary)
    return end
