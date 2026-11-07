"""Pure: the whole computed analysis of a frame: totals, one breakdown per dimension ranked by Herfindahl
(a dimension where the change is concentrated is more informative), the top cross cells of the two
highest-ranked dimensions, the overall significance and the data-quality facts."""

from __future__ import annotations

from dataclasses import dataclass, replace

import stats
from decompose import Breakdown, Seg, breakdown, group
from frame import Frame

MISMATCH_PCT = 1.0
CROSS_CELLS = 10


@dataclass(frozen=True)
class Analysis:
    frame: Frame
    metric: str
    min_n: int
    segments: int
    dims: tuple[Breakdown, ...]       # ranked
    cross: Breakdown | None
    total_test: stats.ZTest | None    # rate only
    mismatch_pct: float | None        # |Σ segments − stated total| / stated total, percent
    summed: tuple[float, ...]

    @property
    def kind(self) -> str:
        return self.frame.kind

    @property
    def top(self) -> Breakdown:
        return self.dims[0]

    @property
    def significance(self) -> str:
        return self.total_test.label if self.total_test else stats.NOT_TESTABLE

    def small_movers(self) -> list[tuple[Breakdown, Seg]]:
        """Top-3 movers (of the top dimension) with a denominator under min_n on either side."""
        if self.kind != "rate":
            return []
        return [(self.top, s) for s in self.top.segs[:3] if min(s.n0, s.n1) < self.min_n]

    def new_in_top3(self) -> list[Seg]:
        return [s for s in self.top.segs[:3] if s.new or s.vanished]


def analyse(frame: Frame, metric: str, min_n: int = stats.DEFAULT_MIN_N) -> Analysis:
    ranked = sorted((breakdown(dim, frame.kind, group(frame.rows, _key(i)), min_n)
                     for i, dim in enumerate(frame.dims)), key=lambda b: (-b.h, frame.dims.index(b.dim)))
    cross = _cross(frame, ranked, min_n)
    summed = tuple(sum(r.vals[k] for r in frame.rows) for k in range(len(frame.rows[0].vals)))
    total_test = None
    if frame.kind == "rate":
        total_test = stats.ztest(summed[0], summed[1], summed[2], summed[3], min_n)
    return Analysis(frame, metric, min_n, len(frame.rows), tuple(ranked), cross, total_test,
                    _mismatch(frame, summed), summed)


def _key(index: int):
    return lambda row: row.keys[index]


def _cross(frame: Frame, ranked: list[Breakdown], min_n: int) -> Breakdown | None:
    if len(ranked) < 2:
        return None
    first, second = (frame.dims.index(b.dim) for b in ranked[:2])
    groups = group(frame.rows, lambda row: f"{row.keys[first]} × {row.keys[second]}")
    if len(groups) <= len({row.keys[first] for row in frame.rows}):
        return None  # the cross adds nothing: every value of the first dimension has one cell
    full = breakdown(f"{ranked[0].dim} × {ranked[1].dim}", frame.kind, groups, min_n)
    return replace(full, segs=full.segs[:CROSS_CELLS])


def _mismatch(frame: Frame, summed: tuple[float, ...]) -> float | None:
    """The largest gap, in percent, between a summed column and the table's own Total row."""
    if not frame.stated_total:
        return None
    gaps = [abs(s - t) / abs(t) * 100 for s, t in zip(summed, frame.stated_total, strict=False) if t]
    return max(gaps) if gaps else None


def is_mismatch(analysis: Analysis) -> bool:
    return analysis.mismatch_pct is not None and analysis.mismatch_pct > MISMATCH_PCT
