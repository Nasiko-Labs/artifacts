"""Pure: cost by model, team and task, totals, period, the team × model cross view and window-over-window change.
Every figure is computed here; unknown-cost rows count as zero cost and are reported by T08."""

from __future__ import annotations

import pandas as pd

from frame import Frame

TOP_N = 10
CROSS_N = 5
CHANGE_N = 5
MIN_CHANGE_DAYS = 14
MAX_WINDOW_DAYS = 30
PER_THOUSAND = 1_000.0


def _ratio(num: float, den: float) -> float | None:
    return float(num) / float(den) if den else None


def _row(key: str, g: pd.DataFrame, total: float) -> dict:
    cost, req = float(g["cost"].sum()), float(g["requests"].sum())
    inp, out, cached = float(g["input"].sum()), float(g["output"].sum()), float(g["cached_in"].sum())
    return {"key": key, "cost": cost, "share_pct": (_ratio(cost, total) or 0.0) * 100, "requests": req,
            "input_tokens": inp, "output_tokens": out,
            "cached_share_pct": None if not inp else cached / inp * 100,
            "avg_cost_per_request": _ratio(cost, req), "avg_input_tokens": _ratio(inp, req),
            "avg_output_tokens": _ratio(out, req),
            "cost_per_1k_output": None if not out else cost / out * PER_THOUSAND}


def group(df: pd.DataFrame, dim: str, top: int = TOP_N) -> list[dict]:
    if dim not in df.columns or df[dim].isna().all():
        return []
    total = float(df["cost"].sum())
    parts = [(str(k), g) for k, g in df.groupby(dim, dropna=False, sort=False)]
    rows = sorted((_row(k, g, total) for k, g in parts), key=lambda r: (-r["cost"], r["key"]))
    if len(rows) <= top:
        return rows
    kept = {r["key"] for r in rows[:top]}
    rest = df[~df[dim].astype(str).isin(kept)]
    return rows[:top] + [{**_row(f"Other ({len(rows) - top})", rest, total), "other": True}]


def period(df: pd.DataFrame) -> dict | None:
    ts = df["ts"].dropna()
    if ts.empty:
        return None
    start, end = ts.min(), ts.max()
    days = int((end - start).days) + 1
    return {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"), "days": days,
            "daily_avg": float(df["cost"].sum()) / days}


def totals(frame: Frame) -> dict:
    df = frame.df
    inp = float(df["input"].sum())
    mix = {k: int(v) for k, v in df["cost_basis"].value_counts().items()}
    return {"cost": float(df["cost"].sum()), "requests": float(df["requests"].sum()), "input_tokens": inp,
            "output_tokens": float(df["output"].sum()),
            "cached_share_pct": None if not inp else float(df["cached_in"].sum()) / inp * 100,
            "currency": "USD", "cost_basis_mix": mix}


def cross(df: pd.DataFrame) -> list[dict]:
    if df["team"].isna().all() or df["model"].isna().all():
        return []
    total = float(df["cost"].sum())
    g = df.groupby(["team", "model"], sort=False)["cost"].sum().sort_values(ascending=False).head(CROSS_N)
    return [{"team": str(t), "model": str(m), "cost": float(c), "share_pct": (_ratio(c, total) or 0.0) * 100}
            for (t, m), c in g.items()]


def change(frame: Frame) -> dict | None:
    """Per agent from the Nasiko `previous` window, else the last N days vs the N before (equal windows, N = half
    the period up to 30) per team (model when there is no team)."""
    previous = frame.meta.get("previous") or {}
    df = frame.df
    if previous:
        cur = df.groupby("team")["cost"].sum()
        pairs = [(str(k), float(previous[k]["cost"]), float(cur.get(k, 0.0))) for k in previous]
        return _changes("this window vs the previous window (from the export)", pairs)
    p = period(df)
    dim = "team" if not df["team"].isna().all() else ("model" if not df["model"].isna().all() else None)
    if p is None or p["days"] < MIN_CHANGE_DAYS or dim is None:
        return None
    window = min(p["days"] // 2, MAX_WINDOW_DAYS)
    end = df["ts"].max()
    cur_mask = df["ts"] > end - pd.Timedelta(days=window)
    prev_mask = (df["ts"] <= end - pd.Timedelta(days=window)) & (df["ts"] > end - pd.Timedelta(days=2 * window))
    cur, prev = df[cur_mask].groupby(dim)["cost"].sum(), df[prev_mask].groupby(dim)["cost"].sum()
    keys = sorted(set(cur.index) | set(prev.index), key=str)
    pairs = [(str(k), float(prev.get(k, 0.0)), float(cur.get(k, 0.0))) for k in keys]
    return _changes(f"last {window} days vs the {window} before, by {dim}", pairs)


def _changes(label: str, pairs: list[tuple[str, float, float]]) -> dict:
    rows = [{"key": k, "prev": p, "cur": c, "change": c - p, "change_pct": None if not p else (c - p) / p * 100}
            for k, p, c in pairs]
    rows.sort(key=lambda r: (-abs(r["change"]), r["key"]))
    return {"label": label, "rows": rows[:CHANGE_N]}
