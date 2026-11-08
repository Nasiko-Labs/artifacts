"""Pure: split deck text into slides and group slides into extraction chunks.

Slides are delimited by a `Slide N` / `Page N` line, a `--- page N ---` marker, or a form feed. Text with
none of these is cut into sections at line breaks (each at most `CHUNK_CHARS`), numbered as "slides" and
said so. At most `MAX_DECK_CHARS` are read; anything past that is reported as not checked.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

MAX_DECK_CHARS = 60_000
CHUNK_CHARS = 8_000
SLIDES_PER_CHUNK = 8

_MARKER = re.compile(r"^[ \t]*(?:-{2,}\s*)?(?:slide|page)\s*#?\s*(\d{1,3})\b[^\n]*$", re.IGNORECASE | re.MULTILINE)
_RANGE = re.compile(r"\bslides?\s+(\d{1,3})\s*(?:-|–|to|through)\s*(\d{1,3})\b", re.IGNORECASE)


@dataclass(frozen=True)
class Slide:
    n: int
    text: str


@dataclass(frozen=True)
class Deck:
    title: str
    slides: tuple[Slide, ...]
    delimited: bool = True
    chars_cut: int = 0  # characters past MAX_DECK_CHARS, not read

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.slides)


def split(text: str, title: str = "") -> Deck:
    body = (text or "").replace("\r\n", "\n")
    cut = max(0, len(body) - MAX_DECK_CHARS)
    body = body[:MAX_DECK_CHARS]
    if "\f" in body:
        parts = [p for p in body.split("\f")]
        slides = tuple(Slide(n, p.strip()) for n, p in enumerate(parts, 1) if p.strip())
        return Deck(title, slides, True, cut)
    markers = list(_MARKER.finditer(body))
    if markers:
        return Deck(title, _by_markers(body, markers), True, cut)
    return Deck(title, tuple(Slide(n, s) for n, s in enumerate(_sections(body), 1)), False, cut)


def _by_markers(body: str, markers: Sequence[re.Match]) -> tuple[Slide, ...]:
    slides: list[Slide] = []
    lead = body[:markers[0].start()].strip()
    for i, marker in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(body)
        text = body[marker.start():end].strip()
        if i == 0 and lead:
            text = f"{lead}\n{text}"
        slides.append(Slide(int(marker.group(1)), text))
    return tuple(slides)


def _sections(body: str) -> list[str]:
    sections: list[str] = []
    current = ""
    for line in body.split("\n"):
        while len(line) > CHUNK_CHARS:  # one enormous line: hard-cut, nothing dropped
            sections, current = _flush(sections, current)
            sections.append(line[:CHUNK_CHARS])
            line = line[CHUNK_CHARS:]
        if len(current) + len(line) + 1 > CHUNK_CHARS:
            sections, current = _flush(sections, current)
        current = f"{current}\n{line}" if current else line
    sections, _ = _flush(sections, current)
    return sections


def _flush(sections: list[str], current: str) -> tuple[list[str], str]:
    return ([*sections, current.strip()] if current.strip() else sections), ""


def chunks(deck: Deck) -> tuple[tuple[Slide, ...], ...]:
    """Groups of at most `SLIDES_PER_CHUNK` slides and about `CHUNK_CHARS` characters."""
    groups: list[tuple[Slide, ...]] = []
    current: tuple[Slide, ...] = ()
    size = 0
    for slide in deck.slides:
        if current and (len(current) >= SLIDES_PER_CHUNK or size + len(slide.text) > CHUNK_CHARS):
            groups.append(current)
            current, size = (), 0
        current, size = (*current, slide), size + len(slide.text)
    if current:
        groups.append(current)
    return tuple(groups)


def slide_range(message: str) -> tuple[int, int] | None:
    found = _RANGE.search(message or "")
    if not found:
        return None
    low, high = sorted((int(found.group(1)), int(found.group(2))))
    return low, high


def only(deck: Deck, wanted: tuple[int, int] | None) -> Deck:
    if wanted is None:
        return deck
    low, high = wanted
    return Deck(deck.title, tuple(s for s in deck.slides if low <= s.n <= high), deck.delimited, deck.chars_cut)
