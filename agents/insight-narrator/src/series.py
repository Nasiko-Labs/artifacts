"""Pure: facts for a time series (shape a). Per numeric column (at most three, ranked by the size of their
change): first to last change and direction, high and low, the biggest single move, the pattern, and the
latest value against the average. Thresholds are named below; the wording is plain English."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from statistics import mean

from display import amount, change, percent
from fact import Draft
from table import Column

FLAT_PCT = 2.0          # within ±2% of the start is "about level"
STEADY_SHARE = 0.8      # this share of moves in one direction is a steady rise or fall
TURN_DISTANCE = 0.15    # an interior high/low this far from both ends is a turn
SAME_AS_AVERAGE_PCT = 2.0
MAX_SERIES = 3


@dataclass(frozen=True)
class _Series:
    name: str
    column: Column
    points: tuple[tuple[str, float], ...]
    change_pct: float | None

    @property
    def magnitude(self) -> float:
        return float("inf") if self.change_pct is None else abs(self.change_pct)


def direction(delta: float, change_pct: float | None) -> str:
    if change_pct is None:
        return "up" if delta > 0 else "down" if delta < 0 else "flat"
    return "up" if change_pct > FLAT_PCT else "down" if change_pct < -FLAT_PCT else "flat"


def series_facts(labels: Sequence[str], columns: Sequence[Column]) -> list[Draft]:
    ranked = sorted((s for c in columns if (s := _series(labels, c)) is not None), key=lambda s: -s.magnitude)
    ranked = ranked[:MAX_SERIES]
    if not ranked:
        return []
    first, rest = ranked[0], ranked[1:]
    out = [_change(s) for s in ranked]
    for s in (first, *rest):
        extra = [_step(s), _pattern(s), _peak(s), _average(s)] if s is first else [_pattern(s)]
        out += [d for d in extra if d is not None]
    return out


def _series(labels: Sequence[str], column: Column) -> _Series | None:
    points = tuple((label, v) for label, v in zip(labels, column.values) if v is not None)
    if len(points) < 2:
        return None
    a, b = points[0][1], points[-1][1]
    pct = None if a == 0 else (b - a) / abs(a) * 100
    return _Series(column.name, column, points, pct)


def _change(s: _Series) -> Draft:
    (l0, a), (ln, b) = s.points[0], s.points[-1]
    unit, delta = s.column.unit, b - a
    way = direction(delta, s.change_pct)
    va, vb, vd = amount(a, unit), amount(b, unit), change(delta, unit)
    vp = percent(s.change_pct) if s.change_pct is not None and not unit.pct else ""
    values = {"first": a, "last": b, "change_abs": delta, "change_pct": s.change_pct, "direction": way}
    if way == "flat":
        return Draft("change", f"{s.name} stayed about level: {va} in {l0} and {vb} in {ln}.", (va, vb), values)
    verb, word, noun = ("rose", "up", "a rise") if way == "up" else ("fell", "down", "a drop")
    size = f"{word} {vd}" if unit.pct else (f"{word} {vp} ({noun} of {vd})" if vp else f"{noun} of {vd}")
    text = f"{s.name} {verb} from {va} in {l0} to {vb} in {ln}, {size}."
    return Draft("change", text, tuple(x for x in (va, vb, vp, vd) if x), values)


def _step(s: _Series) -> Draft | None:
    if len(s.points) < 3:
        return None
    steps = [(s.points[i][0], s.points[i + 1][0], s.points[i + 1][1] - s.points[i][1]) for i in range(len(s.points) - 1)]
    start, end, delta = max(steps, key=lambda t: abs(t[2]))
    if delta == 0:
        return None
    vd = change(delta, s.column.unit)
    word = "up" if delta > 0 else "down"
    return Draft("step", f"The biggest single move in {s.name} was from {start} to {end}: {word} {vd}.", (vd,),
                 {"from": start, "to": end, "change_abs": delta})


def pattern_of(values: Sequence[float]) -> str:
    steps = [b - a for a, b in pairwise(values)]
    if not steps:
        return "flat"
    ups, downs = sum(d > 0 for d in steps), sum(d < 0 for d in steps)
    if ups >= STEADY_SHARE * len(steps):
        return "steady rise"
    if downs >= STEADY_SHARE * len(steps):
        return "steady fall"
    hi, lo = values.index(max(values)), values.index(min(values))
    ends = (values[0], values[-1])
    if 0 < hi < len(values) - 1 and all(_far(values[hi], e) for e in ends):
        return "rose then fell"
    if 0 < lo < len(values) - 1 and all(_far(e, values[lo], base=e) for e in ends):
        return "fell then rose"
    return "choppy"


def _far(high: float, low: float, base: float | None = None) -> bool:
    ref = abs(high if base is None else base)
    return ref > 0 and (high - low) / ref >= TURN_DISTANCE


def _pattern(s: _Series) -> Draft | None:
    if len(s.points) < 3:
        return None
    values = [v for _, v in s.points]
    label, unit = pattern_of(values), s.column.unit
    moves = len(values) - 1
    if label in ("steady rise", "steady fall"):
        word = "rose" if label == "steady rise" else "fell"
        count = sum((b > a) if word == "rose" else (b < a) for a, b in pairwise(values))
        if count == moves:
            return Draft("pattern", f"{s.name} {word} every period, with no step back.", (), {"pattern": label})
        return Draft("pattern", f"{s.name} {word} in {count} of the {moves} moves from one period to the next.",
                     (str(count), str(moves)), {"pattern": label})
    if label in ("rose then fell", "fell then rose"):
        i = values.index(max(values) if label == "rose then fell" else min(values))
        (lt, vt), (ln, vn) = s.points[i], s.points[-1]
        a, b = amount(vt, unit), amount(vn, unit)
        verb, back = ("rose to a high of", "then fell back to") if label == "rose then fell" else \
            ("fell to a low of", "then rose again to")
        return Draft("pattern", f"{s.name} {verb} {a} in {lt}, {back} {b} by {ln}.", (a, b), {"pattern": label})
    if label == "choppy":
        return Draft("pattern", f"{s.name} moved up and down rather than in one direction.", (), {"pattern": label})
    return None


def _peak(s: _Series) -> Draft | None:
    values = [v for _, v in s.points]
    hi, lo = values.index(max(values)), values.index(min(values))
    if {hi, lo} <= {0, len(values) - 1}:
        return None  # the change fact already says it
    (lh, vh), (ll, vl) = s.points[hi], s.points[lo]
    a, b = amount(vh, s.column.unit), amount(vl, s.column.unit)
    return Draft("peak", f"{s.name} was highest in {lh} ({a}) and lowest in {ll} ({b}).", (a, b),
                 {"peak": vh, "peak_label": lh, "trough": vl, "trough_label": ll})


def _average(s: _Series) -> Draft | None:
    values = [v for _, v in s.points]
    avg, (ln, last) = mean(values), s.points[-1]
    if avg == 0:
        return None
    rel = (last / avg - 1) * 100
    va, vl = amount(avg, s.column.unit), amount(last, s.column.unit)
    if abs(rel) < SAME_AS_AVERAGE_PCT:
        return Draft("average", f"In {ln}, {s.name} stood at {vl}, about the same as the average across the table "
                                f"({va}).", (vl, va), {"latest_vs_average_pct": rel})
    side = "above" if rel > 0 else "below"
    vr = percent(rel)
    return Draft("average", f"In {ln}, {s.name} stood at {vl}, {vr} {side} the average across the table ({va}).",
                 (vl, vr, va), {"latest_vs_average_pct": rel})
