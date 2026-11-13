"""Pure: criteria weights and weighted scores, all in code.

Weights: `N%`, a bare 1-5, or high/medium/low (3/2/1), normalised to sum 100; an unweighted criterion
gets the mean of the given weights; none given = equal weights. Score per option =
sum(weight_i x rating_i) / 5, 0-100, rounded to an integer. Ranked by score, then fewer cons, then input
order. A top-two gap under CLOSE_CALL_POINTS is a close call."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from text import normalise

CLOSE_CALL_POINTS = 5
MAX_CRITERIA = 8
NAME_MAX_WORDS = 5  # a longer "criterion" is a sentence that followed the list
MIN_RATING, MAX_RATING = 1, 5
_LEVELS = {"high": 3.0, "medium": 2.0, "med": 2.0, "low": 1.0}
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_LEVEL = re.compile(r"\b(high|medium|med|low)\b", re.IGNORECASE)
_SMALL = re.compile(r"(?:^|\s|\()([1-5])\s*\)?\s*$")
_SPLIT = re.compile(r"[,;\n]+|\s+and\s+")
_NAME_JUNK = re.compile(r"\b(?:weight(?:ed)?|priority|importance)\b|[()\[\]:=*•-]+", re.IGNORECASE)


@dataclass(frozen=True)
class Criterion:
    name: str
    weight: float
    explicit: bool = False


def _one(item: str) -> tuple[str, tuple[float | None, str]]:
    raw: float | None = None
    kind, rest = "", item
    for kind_name, pattern, value in (("pct", _PCT, lambda m: float(m.group(1))),
                                      ("level", _LEVEL, lambda m: _LEVELS[m.group(1).lower()]),
                                      ("scale", _SMALL, lambda m: float(m.group(1)))):
        found = pattern.search(rest)
        if found:
            raw, kind = value(found), kind_name
            rest = rest[:found.start()] + " " + rest[found.end():]
            break
    name = " ".join(_NAME_JUNK.sub(" ", rest).split()).strip(" .").casefold()
    return name, (raw, kind)


def parse(body: str) -> tuple[Criterion, ...]:
    items = [_one(part.strip(" -*•\t")) for part in _SPLIT.split(body or "") if part.strip(" -*•\t.")]
    items = [(name, raw) for name, raw in items if name and len(name.split()) <= NAME_MAX_WORDS][:MAX_CRITERIA]
    if any(kind == "pct" for _, (_, kind) in items):  # mixed scales: only the percentages count
        return normalised([(n, r if kind == "pct" else None) for n, (r, kind) in items])
    return normalised([(n, r) for n, (r, _) in items])


def normalised(items: Sequence[tuple[str, float | None]]) -> tuple[Criterion, ...]:
    given = [r for _, r in items if r is not None and r > 0]
    fill = sum(given) / len(given) if given else 1.0
    raws = [(n, r if r is not None and r > 0 else fill, r is not None and r > 0) for n, r in items]
    total = sum(r for _, r, _ in raws) or 1.0
    return tuple(Criterion(n, r * 100.0 / total, explicit) for n, r, explicit in raws)


def reweight(current: Sequence[Criterion], changes: Mapping[str, float]) -> tuple[Criterion, ...]:
    """New percentages for the named criteria; the others share the rest in their old proportions."""
    fixed = {c.name: changes[c.name] for c in current if c.name in changes}
    left = max(0.0, 100.0 - sum(fixed.values()))
    others = [c for c in current if c.name not in fixed]
    old = sum(c.weight for c in others) or 1.0
    items = [(c.name, fixed[c.name] if c.name in fixed else left * c.weight / old) for c in current]
    return normalised([(n, w if w > 0 else 0.0001) for n, w in items])


def key_of(name: str, criteria: Sequence[Criterion]) -> str:
    norm = normalise(name)
    exact = [c.name for c in criteria if c.name == norm]
    if exact:
        return exact[0]
    loose = [c.name for c in criteria if norm and (norm in c.name or c.name in norm)]
    return loose[0] if len(loose) == 1 else ""


def is_weighted(criteria: Sequence[Criterion]) -> bool:
    return any(c.explicit for c in criteria)


def describe(criteria: Sequence[Criterion]) -> str:
    return ", ".join(f"{c.name} {round(c.weight)}%" for c in criteria)


def score(ratings: Mapping[str, Mapping[str, int]], criteria: Sequence[Criterion], key: str) -> int:
    total = sum(c.weight * ratings[key][c.name] for c in criteria) / MAX_RATING
    return int(total + 0.5)


@dataclass(frozen=True)
class Ranking:
    scores: dict[str, int]
    ranked: tuple[str, ...]
    close_call: bool


def rank(keys: Sequence[str], ratings: Mapping[str, Mapping[str, int]], criteria: Sequence[Criterion],
         cons: Mapping[str, int]) -> Ranking:
    scores = {k: score(ratings, criteria, k) for k in keys}
    order = sorted(range(len(keys)), key=lambda i: (-scores[keys[i]], cons.get(keys[i], 0), i))
    ranked = tuple(keys[i] for i in order)
    close = len(ranked) >= 2 and scores[ranked[0]] - scores[ranked[1]] < CLOSE_CALL_POINTS
    return Ranking(scores, ranked, close)
