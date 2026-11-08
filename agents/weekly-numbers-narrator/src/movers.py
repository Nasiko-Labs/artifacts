"""Pure: deltas, percentages, materiality and ranking, all in code.

delta = current - previous; pct = delta / |previous| (undefined when previous is 0). A metric is a mover
when |pct| >= 5% (or |delta| >= the sheet's threshold for it) and, when the series has at least four
prior points, the current value breaks out of the range of the last four of them (so noise is not
narrated). Movers are ranked by |pct| (undefined last), at most four. |pct| < 1% is flat."""

from __future__ import annotations

from dataclasses import dataclass

from periods import Selection
from table import Column, Table, parse_number

MATERIAL_PCT = 0.05
FLAT_PCT = 0.01
MIN_PRIOR_POINTS = 4
NOISE_WINDOW = 4
MAX_MOVERS = 4


@dataclass(frozen=True)
class Delta:
    metric: str
    column: Column
    current: float
    previous: float
    delta: float
    pct: float | None
    direction: str          # "up" | "down" | "flat"
    prior_points: int
    in_noise: bool          # the current value stays inside the recent range
    material: bool
    rank: int = 0

    @property
    def is_mover(self) -> bool:
        return self.material and not self.in_noise


@dataclass(frozen=True)
class Movement:
    deltas: tuple[Delta, ...]
    movers: tuple[Delta, ...]
    flat: tuple[str, ...]
    skipped: tuple[str, ...]          # metrics without a value in both periods
    few_points: bool                  # some series had fewer than four prior points


def change(current: float, previous: float) -> tuple[float, float | None]:
    delta = current - previous
    return delta, (None if previous == 0 else delta / abs(previous))


def direction_of(delta: float, pct: float | None) -> str:
    if delta == 0 or (pct is not None and abs(pct) < FLAT_PCT):
        return "flat"
    return "up" if delta > 0 else "down"


def measure(table: Table, selection: Selection) -> Movement:
    if selection.current is None or selection.previous is None:
        return Movement((), (), (), (), False)
    deltas, skipped = [], []
    for column in table.metrics:
        found = _delta(table, selection, column)
        if found is None:
            skipped.append(column.name)
        else:
            deltas.append(found)
    ranked = sorted((d for d in deltas if d.is_mover), key=_rank_key)[:MAX_MOVERS]
    movers = tuple(_with_rank(d, i + 1) for i, d in enumerate(ranked))
    flat = tuple(d.metric for d in deltas if d.direction == "flat")
    few = any(d.prior_points < MIN_PRIOR_POINTS for d in deltas)
    return Movement(tuple(deltas), movers, flat, tuple(skipped), few)


def _delta(table: Table, selection: Selection, column: Column) -> Delta | None:
    cur = parse_number(table.rows[selection.current][column.index])
    prev = parse_number(table.rows[selection.previous][column.index])
    if cur is None or prev is None:
        return None
    delta, pct = change(cur.value, prev.value)
    prior = [n.value for n in (parse_number(table.rows[i][column.index]) for i in selection.prior) if n is not None]
    window = prior[-NOISE_WINDOW:]
    in_noise = len(prior) >= MIN_PRIOR_POINTS and min(window) <= cur.value <= max(window)
    threshold = table.threshold(column.name)
    material = (pct is not None and abs(pct) >= MATERIAL_PCT) or (threshold is not None and abs(delta) >= threshold)
    direction = direction_of(delta, pct)
    if material and direction == "flat" and delta != 0:  # moved past the sheet's own threshold
        direction = "up" if delta > 0 else "down"
    return Delta(column.name, column, cur.value, prev.value, delta, pct, direction, len(prior), in_noise, material)


def _rank_key(d: Delta) -> tuple[int, float, float]:
    return (1, 0.0, -abs(d.delta)) if d.pct is None else (0, -abs(d.pct), -abs(d.delta))


def _with_rank(d: Delta, rank: int) -> Delta:
    return Delta(d.metric, d.column, d.current, d.previous, d.delta, d.pct, d.direction, d.prior_points, d.in_noise,
                 d.material, rank)
