"""Pure: what drove each top service mover. Within that service, the values of every dimension the export has
(usage type, region, account, tags) ranked by their own window-over-window change; the top 3 become evidence
rows `m1.d1` … with their delta and their share of the service's change."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from stats import Mover, gross
from windows import Windows, in_window

DRIVERS_PER_MOVER = 3
PRIORITY = ("usage_type", "region", "account")
DIMENSION_LABELS = {"usage_type": "usage type", "region": "region", "account": "account", "line_type": "line type"}


@dataclass(frozen=True)
class Driver:
    id: str
    dimension: str
    value: str
    delta: float
    share_pct: float | None

    def to_dict(self) -> dict:
        return {"id": self.id, "dimension": self.dimension, "value": self.value, "delta": self.delta,
                "share_pct": self.share_pct}


def label(dimension: str) -> str:
    return f"tag {dimension[4:]}" if dimension.startswith("tag:") else DIMENSION_LABELS.get(dimension, dimension)


def drivers(df: pd.DataFrame, windows: Windows, movers: list[Mover], dims: tuple[str, ...]) -> list[list[Driver]]:
    if not windows.comparable:
        return [[] for _ in movers]
    g = gross(df)
    cur_mask, prev_mask = in_window(g["date"], windows.cur), in_window(g["date"], windows.prev)
    out = []
    for n, mover in enumerate(movers, start=1):
        rows = g[g["service"] == mover.name]
        cands: list[tuple[str, str, float]] = []
        for dim in dims:
            if dim == "line_type":
                continue
            cur = rows[cur_mask.loc[rows.index]].groupby(dim)["cost"].sum()
            prev = rows[prev_mask.loc[rows.index]].groupby(dim)["cost"].sum()
            delta = cur.sub(prev, fill_value=0.0)
            for value, d in delta.items():
                if str(value).strip() and abs(float(d)) > 0:
                    cands.append((dim, str(value), float(d)))
        same_sign = [c for c in cands if (c[2] > 0) == (mover.change_abs > 0)]
        ranked = _distinct(sorted(same_sign or cands, key=lambda c: (-round(abs(c[2]), 2), _priority(c[0]), c[1])))
        change = mover.change_abs
        out.append([Driver(f"m{n}.d{i}", dim, value, d, (d / change * 100) if change else None)
                    for i, (dim, value, d) in enumerate(ranked, start=1)])
    return out


def _priority(dim: str) -> int:
    return PRIORITY.index(dim) if dim in PRIORITY else len(PRIORITY)


def _distinct(ranked: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]:
    """The top drivers, skipping one whose delta equals an already kept one (the same rows seen through
    another dimension, e.g. the account of the only usage type that moved)."""
    kept: list[tuple[str, str, float]] = []
    for cand in ranked:
        if all(abs(cand[2] - k[2]) > 0.005 * abs(k[2]) + 0.01 for k in kept):
            kept.append(cand)
        if len(kept) == DRIVERS_PER_MOVER:
            break
    return kept
