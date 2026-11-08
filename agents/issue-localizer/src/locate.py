"""Pure: map the model's quoted line(s) to line numbers inside the symbol's span. Found → `precision: "line"`
with one line of context each side (clamped to the span); not found → the symbol's whole span with
`precision: "symbol"`. Lines past the visible part of a truncated file are never claimed."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from skeleton_types import Symbol

MAX_QUOTE_LINES = 3
MIN_QUOTE_CHARS = 4
_SMART = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_SPACE = re.compile(r"\s+")
_NUMBER_PREFIX = re.compile(r"^\s*\d+\s*[|:]\s?")


@dataclass(frozen=True)
class Location:
    path: str
    symbol: str
    start_line: int
    end_line: int
    precision: str
    quote: str


def normalise(text: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text or "").translate(_SMART)).strip()


def code_lines(text: str, symbol: Symbol, visible_lines: int) -> list[str]:
    lines = text.split("\n")
    end = min(symbol.end, visible_lines, len(lines))
    return lines[symbol.start - 1:end]


def find(path: str, text: str, symbol: Symbol, quote: str, visible_lines: int) -> Location:
    lines = text.split("\n")
    last = min(symbol.end, visible_lines, len(lines))
    span_end = max(symbol.start, last)
    wanted = [normalise(_NUMBER_PREFIX.sub("", q)) for q in (quote or "").split("\n")]
    wanted = [w for w in wanted if w][:MAX_QUOTE_LINES]
    hit = _match(lines, symbol.start, last, wanted) if wanted and len(" ".join(wanted)) >= MIN_QUOTE_CHARS else None
    if hit is None:
        return Location(path, symbol.name, symbol.start, span_end, "symbol", "")
    first, final = hit
    exact = "\n".join(lines[i - 1].strip() for i in range(first, final + 1))
    return Location(path, symbol.name, max(symbol.start, first - 1), min(span_end, final + 1), "line", exact)


def _match(lines: list[str], start: int, end: int, wanted: list[str]) -> tuple[int, int] | None:
    """1-based (first, last) of consecutive lines whose normalised text contains each quoted line."""
    for i in range(start, end - len(wanted) + 2):
        if all(i + k <= end and wanted[k] in normalise(lines[i + k - 1]) for k in range(len(wanted))):
            return i, i + len(wanted) - 1
    return None
