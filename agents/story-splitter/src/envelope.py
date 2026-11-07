"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from outline import Outline
from stories import Story, to_output

HIGH_MIN_EPIC_WORDS = 80
LOW_MAX_EPIC_WORDS = 40
HIGH_MAX_PLACEHOLDERS = 1
LOW_MIN_PLACEHOLDERS = 4
_PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,40}\?\]")


@dataclass(frozen=True)
class Checks:
    """What the checks did this run (or since the epic was split, for refinements)."""

    dropped: tuple[str, ...] = ()
    merged: tuple[str, ...] = ()
    capped: tuple[str, ...] = ()
    actors_replaced: int = 0
    dropped_criteria: int = 0
    broken_cycles: int = 0
    notes: tuple[str, ...] = field(default=())


def placeholders(stories: list[Story]) -> int:
    texts = [t for s in stories for t in (s.as_a, s.i_want, s.so_that, s.title, *s.acceptance)]
    return sum(len(_PLACEHOLDER.findall(t)) for t in texts)


def uncovered(stories: list[Story], outline: Outline) -> list[str]:
    used = {b for s in stories for b in s.basis}
    return [c["snippet"] for c in outline.capabilities if c["id"] not in used]


def confidence(stories: list[Story], outline: Outline, epic_words: int) -> str:
    holes = placeholders(stories)
    large = any(s.size == "L" for s in stories)
    gaps = bool(uncovered(stories, outline))
    if large or holes >= LOW_MIN_PLACEHOLDERS or gaps or epic_words < LOW_MAX_EPIC_WORDS:
        return "low"
    if holes <= HIGH_MAX_PLACEHOLDERS and epic_words >= HIGH_MIN_EPIC_WORDS:
        return "high"
    return "medium"


def summary(stories: list[Story], skeleton: str, gaps: int) -> str:
    sizes = Counter(s.size for s in stories)
    mix = ", ".join(f"{sizes[k]} {k}" for k in ("S", "M", "L") if sizes[k])
    first = next((s.title for s in stories if s.key == skeleton), stories[0].title if stories else "")
    noun = "story" if len(stories) == 1 else "stories"
    text = f"{len(stories)} {noun} ({mix}); first slice: {first}"
    if gaps:
        text += f"; {gaps} {'capability' if gaps == 1 else 'capabilities'} not covered"
    return text


def build(stories: list[Story], skeleton: str, outline: Outline, epic_words: int, checks: Checks) -> dict:
    items = to_output(stories, skeleton)
    level = confidence(stories, outline, epic_words)
    gaps = uncovered(stories, outline)
    needs = level == "low" or any(s.is_spike for s in stories) or bool(checks.dropped)
    return {"summary": summary(stories, skeleton, len(gaps)), "items": items, "stories": items,
            "uncovered": gaps, "open_questions": [u["text"] for u in outline.unknowns],
            "confidence": level, "needs_human": needs, "placeholders": placeholders(stories),
            "actors_replaced": checks.actors_replaced, "dropped": list(checks.dropped),
            "merged": list(checks.merged), "capped": list(checks.capped),
            "dropped_criteria": checks.dropped_criteria, "epic_words": epic_words, "chunks": outline.chunks}
