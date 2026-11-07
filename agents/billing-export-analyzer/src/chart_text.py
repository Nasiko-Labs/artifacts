"""Pure: the chart as text (the chat renders markdown only). Spend by service per bucket (weekly under 90 days,
else monthly) for the top 6 services + Other + Total, a sparkline per row, and a bar block for the movers.
`chart_data` carries the same numbers for any client to draw its own."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from digest import money
from stats import gross

TOP_SERVICES = 6
WEEKLY_UNDER_DAYS = 90
SPARKS = "▁▂▃▄▅▆▇█"
BAR_WIDTH = 20
OTHER, TOTAL = "Other", "Total"


def buckets(df: pd.DataFrame, monthly: bool, days: int) -> pd.Series:
    """Bucket label per row: 'YYYY-MM' for monthly buckets, else the start of the 7-day bucket ('MM-DD')."""
    if monthly or days >= WEEKLY_UNDER_DAYS:
        return df["date"].dt.strftime("%Y-%m")
    start = df["date"].min()
    offset = ((df["date"] - start).dt.days // 7) * 7
    return (start + pd.to_timedelta(offset, unit="D")).dt.strftime("%m-%d")


def chart_data(df: pd.DataFrame, monthly: bool, days: int) -> dict:
    g = gross(df)
    if g.empty:
        return {"x": [], "series": {}}
    labels = buckets(g, monthly, days)
    table = g.assign(bucket=labels).pivot_table(index="service", columns="bucket", values="cost", aggfunc="sum",
                                                fill_value=0.0)
    table = table.reindex(sorted(table.columns), axis=1)
    order = table.sum(axis=1).sort_values(ascending=False).index
    top, rest = list(order[:TOP_SERVICES]), list(order[TOP_SERVICES:])
    series = {str(s): [round(float(v), 2) for v in table.loc[s]] for s in top}
    if rest:
        series[OTHER] = [round(float(v), 2) for v in table.loc[rest].sum(axis=0)]
    series[TOTAL] = [round(float(v), 2) for v in table.sum(axis=0)]
    return {"x": [str(c) for c in table.columns], "series": series}


def sparkline(values: Sequence[float]) -> str:
    if not values:
        return ""
    low, high = min(values), max(values)
    if high == low:
        return SPARKS[0] * len(values)
    return "".join(SPARKS[round((v - low) / (high - low) * (len(SPARKS) - 1))] for v in values)


def _short(value: float, currency: str | None) -> str:
    return money(value, currency)


def table(data: Mapping[str, Any], currency: str | None) -> str:
    x, series = data["x"], data["series"]
    if not x:
        return "No spend to chart."
    head = "| Service | Trend | " + " | ".join(x) + " |"
    sep = "|---|---|" + "---|" * len(x)
    rows = []
    for name, values in series.items():
        label = f"**{name}**" if name == TOTAL else name
        trend = f"{sparkline(values)} {_short(min(values), currency)} → {_short(max(values), currency)}"
        cells = " | ".join(money(v, currency) for v in values)
        rows.append(f"| {label} | {trend} | {cells} |")
    return "\n".join([head, sep, *rows])


def mover_bars(movers: Sequence[Mapping[str, Any]], currency: str | None) -> str:
    if not movers:
        return ""
    largest = max(abs(m["change_abs"]) for m in movers) or 1.0
    width = max(len(m["name"]) for m in movers)
    lines = []
    for m in movers:
        n = max(1, round(abs(m["change_abs"]) / largest * BAR_WIDTH))
        lines.append(f"{m['name']:<{width}}  {'█' * n:<{BAR_WIDTH}}  {money(m['change_abs'], currency, signed=True)}")
    return "```\n" + "\n".join(lines) + "\n```"


def render(data: Mapping[str, Any], movers: Sequence[Mapping[str, Any]], currency: str | None, unit: str) -> str:
    parts = [f"Spend by service per {unit} (Trend: min → max)", table(data, currency)]
    bars = mover_bars(movers, currency)
    if bars:
        parts += ["Top movers", bars]
    return "\n\n".join(parts)
