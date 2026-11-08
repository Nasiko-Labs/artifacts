"""Pure: where a text may be cut. Every split is a list of contiguous spans over the text itself, so the
parts rejoin to the input exactly; a span's `view_start` (<= start) adds the requested overlap.

Units per strategy: speaker turns, headings (shallowest level used more than once), paragraphs, sentences. A unit
larger than the part size is cut at a finer boundary (paragraph, line, sentence, whitespace, then a
hard cut) and both parts around that cut are flagged `mid`."""

from __future__ import annotations

import itertools
import math
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

STRATEGIES = ("turns", "headings", "paragraphs", "sentences")
UNIT_WORDS = {"turns": "turns", "headings": "sections", "paragraphs": "paragraphs", "sentences": "sentences"}
MIN_TURN_LINES = 5
MIN_HEADINGS = 3
MAX_PART_STRETCH = 1.25
ABBREVIATIONS = frozenset({"mr.", "mrs.", "ms.", "dr.", "e.g.", "i.e.", "vs.", "inc.", "no.", "st.", "jr.", "sr."})

_NAME = r"[A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,3}"
_TURN = re.compile(
    rf"^(?:(?:\[\d{{1,2}}:\d{{2}}(?::\d{{2}})?\]\s*)?{_NAME}:[ \t]|{_NAME}[ \t]+\d{{1,2}}:\d{{2}}(?::\d{{2}})?[ \t]*$"
    r"|\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}[ \t]*-->)", re.MULTILINE)
_MD_HEADING = re.compile(r"^(#{1,6})[ \t]+\S", re.MULTILINE)
_NUM_HEADING = re.compile(r"^(\d+(?:\.\d+)*)[.)]?[ \t]+[A-Z][^\n]{0,80}$", re.MULTILINE)
_PARAGRAPH_GAP = re.compile(r"\n[ \t]*\n\s*")
_LINE_END = re.compile(r"\n")
_SENTENCE_GAP = re.compile(r"[.!?][\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")
_LAST_TOKEN = re.compile(r"(\S+)$")
_SPACE_RUN = re.compile(r"\s+")


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    view_start: int
    mid: bool = False


def turn_starts(text: str) -> list[int]:
    return [m.start() for m in _TURN.finditer(text)]


def heading_starts(text: str) -> list[int]:
    found = [(m.start(), len(m.group(1))) for m in _MD_HEADING.finditer(text)]
    if len(found) < MIN_HEADINGS:
        found = [(m.start(), m.group(1).count(".") + 1) for m in _NUM_HEADING.finditer(text)]
    if not found:
        return []
    levels = sorted({level for _, level in found})
    # The shallowest level used more than once: a lone document title does not set the level.
    top = next((lv for lv in levels if sum(1 for _, x in found if x == lv) > 1), levels[0])
    return [at for at, level in found if level == top]


def paragraph_starts(text: str) -> list[int]:
    return [m.end() for m in _PARAGRAPH_GAP.finditer(text) if m.end() < len(text)]


def sentence_starts(text: str) -> list[int]:
    starts = []
    for m in _SENTENCE_GAP.finditer(text):
        token = _LAST_TOKEN.search(text[max(0, m.start() - 12):m.start() + 1])
        if token and token.group(1).casefold() in ABBREVIATIONS:
            continue
        if m.end() < len(text):
            starts.append(m.end())
    return starts


FINDERS = {"turns": turn_starts, "headings": heading_starts, "paragraphs": paragraph_starts,
           "sentences": sentence_starts}


def unit_starts(text: str, strategy: str) -> list[int]:
    """Sorted unit start offsets, always beginning with 0."""
    return sorted({0, *FINDERS[strategy](text)})


def detect(text: str) -> str:
    if len(turn_starts(text)) >= MIN_TURN_LINES:
        return "turns"
    if len(heading_starts(text)) >= MIN_HEADINGS:
        return "headings"
    return "paragraphs" if paragraph_starts(text) else "sentences"


def finer_cut(text: str, lo: int, hi: int) -> int:
    """A cut in (lo, hi], preferring the latest paragraph, line, sentence, then whitespace boundary in
    the upper half of the window; a hard cut at `hi` when there is none."""
    floor = lo + max(1, (hi - lo) // 2)
    window = text[floor:hi]
    for pattern in (_PARAGRAPH_GAP, _LINE_END, _SENTENCE_GAP, _SPACE_RUN):
        ends = [floor + m.end() for m in pattern.finditer(window)]
        ends = [e for e in ends if lo < e <= hi]
        if ends:
            return ends[-1]
    return hi


def pack_by_size(text: str, starts: list[int], size: int) -> tuple[list[int], set[int]]:
    """(cut offsets, non-structural cuts): whole units packed while the part stays within `size`."""
    cuts, mids, pos = [], set(), 0
    while len(text) - pos > size:
        hi = pos + size
        best = starts[bisect_right(starts, hi) - 1]
        if best > pos:
            cut = best
        else:
            cut = finer_cut(text, pos, hi)
            mids.add(cut)
        cuts.append(cut)
        pos = cut
    return cuts, mids


def pack_into(text: str, starts: list[int], parts: int) -> tuple[list[int], set[int]]:
    """Cuts for `parts` even parts: each at the structural break nearest the ideal offset, never
    letting a part grow beyond 125% of the even size."""
    length = len(text)
    cap = math.floor(math.ceil(length / parts) * MAX_PART_STRETCH)
    cuts, mids, pos = [], set(), 0
    for k in range(1, parts):
        ideal, lo, hi = round(k * length / parts), pos + 1, min(pos + cap, length - 1)
        if lo > hi:
            break
        idx = bisect_left(starts, ideal)
        near = [s for s in starts[max(0, idx - 1):idx + 1] if lo <= s <= hi]
        if near:
            cut = min(near, key=lambda s: (abs(s - ideal), s))
        else:
            cut = finer_cut(text, pos, min(max(ideal, lo), hi))
            mids.add(cut)
        cuts.append(cut)
        pos = cut
    return cuts, mids


def to_spans(length: int, cuts: list[int], mids: set[int]) -> list[Span]:
    edges = [0, *cuts, length]
    return [Span(a, b, a, a in mids or b in mids) for a, b in itertools.pairwise(edges) if b > a]


def with_overlap(text: str, spans: list[Span], starts: list[int], units: int, chars: int) -> list[Span]:
    """Each part after the first also shows the end of the previous part: `units` whole units back
    (when it starts on a unit), else `chars` characters back, moved forward to a word start."""
    out = spans[:1]
    for prev, span in itertools.pairwise(spans):
        view = span.start
        if units and span.start in starts:
            view = starts[max(0, starts.index(span.start) - units)]
        elif chars:
            view = max(0, span.start - chars)
            gap = _SPACE_RUN.search(text, view, span.start)
            view = gap.end() if gap and view > 0 else view
        out.append(Span(span.start, span.end, max(view, prev.start), span.mid))
    return out


def unit_label(starts: list[int], strategy: str, span: Span) -> str:
    first = bisect_right(starts, span.start)
    last = bisect_right(starts, span.end - 1)
    return f"{UNIT_WORDS[strategy]} {first}-{last}" if last > first else f"{UNIT_WORDS[strategy][:-1]} {first}"
