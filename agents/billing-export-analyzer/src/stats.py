"""Pure: totals, top movers, untagged share and the budget run-rate, all in pandas. Movers and anomalies use
gross usage only (credits are kept apart)."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass

import pandas as pd

from frame import Frame, parse_money
from windows import Windows, in_window

TOP_MOVERS = 5
NOISE_FLOOR_ABS = 1.00
NOISE_FLOOR_SHARE = 0.005
PREFERRED_TAGS = ("team", "owner", "env", "application", "project")
UNTAGGED_VALUES = frozenset({"", "(no tag)", "untagged", "none", "null", "n/a", "(not tagged)", "nan"})
NO_TAG_NOTE = "Untagged share can't be computed: no tag column in this export."
_BUDGET = re.compile(r"\bbudget\b[^$\d]{0,20}([$€£]?\s*[\d][\d,]*(?:\.\d+)?\s*[kKmM]?)", re.IGNORECASE)
_MONTHS = {m.casefold(): i for i, m in enumerate(calendar.month_name) if m}


def gross(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["credit"]]


def totals(frame: Frame, windows: Windows) -> dict:
    df = frame.df
    g = float(gross(df)["cost"].sum())
    credits = float(df[df["credit"]]["cost"].sum())
    return {"gross": g, "credits": credits, "net": g + credits, "daily_avg": g / max(windows.days, 1),
            "currency": frame.currency}


def service_totals(df: pd.DataFrame) -> dict[str, float]:
    series = gross(df).groupby("service")["cost"].sum().sort_values(ascending=False)
    return {str(k): float(v) for k, v in series.items()}


@dataclass(frozen=True)
class Mover:
    name: str
    dimension: str
    prev: float
    cur: float

    @property
    def change_abs(self) -> float:
        return self.cur - self.prev

    @property
    def change_pct(self) -> float | None:
        return None if self.prev == 0 else self.change_abs / self.prev * 100

    @property
    def status(self) -> str:
        return "new" if self.prev == 0 else "gone" if self.cur == 0 else ""


def movers(df: pd.DataFrame, windows: Windows, dimension: str = "service", top: int = TOP_MOVERS) -> list[Mover]:
    if not windows.comparable or dimension not in df:
        return []
    g = gross(df)
    cur = g[in_window(g["date"], windows.cur)].groupby(dimension)["cost"].sum()
    prev = g[in_window(g["date"], windows.prev)].groupby(dimension)["cost"].sum()
    both = pd.concat([prev.rename("prev"), cur.rename("cur")], axis=1).fillna(0.0)
    floor = max(NOISE_FLOOR_ABS, NOISE_FLOOR_SHARE * float(prev.sum()))
    rows = [Mover(str(name), dimension, float(r.prev), float(r.cur)) for name, r in both.iterrows()
            if str(name).strip() and abs(float(r.cur) - float(r.prev)) >= floor]
    rows.sort(key=lambda m: (-abs(m.change_abs), -m.cur, m.name))
    return rows[:top]


def window_sum(df: pd.DataFrame, bounds: tuple[pd.Timestamp, pd.Timestamp]) -> float:
    g = gross(df)
    return float(g[in_window(g["date"], bounds)]["cost"].sum())


def pick_tag(tag_keys: tuple[str, ...], df: pd.DataFrame, named: str = "") -> str | None:
    if not tag_keys:
        return None
    low = (named or "").casefold()
    for key in tag_keys:
        if re.search(rf"\b{re.escape(key.casefold())}\b", low):
            return key
    for key in PREFERRED_TAGS:
        if key in tag_keys:
            return key
    return max(tag_keys, key=lambda k: int((~_is_untagged(df[f"tag:{k}"])).sum()))


def _is_untagged(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.strip().str.casefold().isin(UNTAGGED_VALUES)


def untagged(frame: Frame, named: str = "") -> dict | None:
    key = pick_tag(frame.tag_keys, frame.df, named)
    if key is None:
        return None
    g = gross(frame.df)
    total = float(g["cost"].sum())
    mask = _is_untagged(g[f"tag:{key}"])
    cost = float(g[mask]["cost"].sum())
    top = g[mask].groupby("service")["cost"].sum().sort_values(ascending=False).head(5)
    return {"tag": key, "cost": cost, "share_pct": cost / total * 100 if total else 0.0,
            "top_services": [{"service": str(s), "cost": float(c)} for s, c in top.items()]}


def parse_budget(text: str) -> tuple[float, int | None] | None:
    """(amount, month number or None) from "budget $40,000 for November" / "budget $190k"."""
    match = _BUDGET.search(text or "")
    if not match:
        return None
    raw = match.group(1).replace(" ", "")
    scale = {"k": 1_000, "m": 1_000_000}.get(raw[-1].casefold(), 1)
    amount = parse_money(raw.rstrip("kKmM"))
    if amount is None or amount <= 0:
        return None
    tail = (text or "")[match.end():match.end() + 40].casefold()
    month = next((n for name, n in _MONTHS.items() if re.search(rf"\b{name}\b", tail)), None)
    return amount * scale, month


def budget(df: pd.DataFrame, windows: Windows, amount: float, month: int | None) -> dict | None:
    if windows.monthly:
        return None
    g = gross(df)
    end = windows.end
    candidates = g[g["date"].dt.month == month] if month else g[g["date"].dt.to_period("M") == end.to_period("M")]
    if candidates.empty:
        return None
    period = candidates["date"].max().to_period("M")
    in_month = g[g["date"].dt.to_period("M") == period]
    spent = float(in_month["cost"].sum())
    elapsed = int((in_month["date"].max() - period.start_time).days) + 1
    in_days = period.days_in_month
    projected = spent / elapsed * in_days
    return {"month": period.strftime("%Y-%m"), "amount": amount, "spent_to_date": spent, "days_elapsed": elapsed,
            "days_in_month": in_days, "projected": projected,
            "projected_vs_budget_pct": (projected - amount) / amount * 100,
            "label": "run-rate projection, assumes the rest of the month looks like so far"}
