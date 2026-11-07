"""Pure: "what if gpt-4o-mini replaced gpt-4o for the small calls", recomputed in code from the price table with
no model call. Labelled a what-if; the stored analysis is never changed by it."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

import fmt
import prices
from aggregate import period
from frame import Frame
from units import unit_keys
from waste import T04_MAX_AVG_INPUT, T04_MAX_AVG_OUTPUT, monthly

_MODEL = r"([a-z0-9][\w.\-/:]*[a-z0-9])"
_NEW_REPLACES_OLD = re.compile(rf"\b{_MODEL}\s+(?:replaced|replaces|replace|instead of)\s+{_MODEL}", re.IGNORECASE)
_USE_NEW_INSTEAD = re.compile(rf"\b(?:use|used|using|switch(?:ed)? to|move(?:d)? to)\s+{_MODEL}\s+(?:instead of|"
                              rf"for|over)\s+{_MODEL}", re.IGNORECASE)
_SWAP_OLD_WITH_NEW = re.compile(rf"\b(?:swap|swapped|replace|replaced)\s+{_MODEL}\s+(?:with|by|for)\s+{_MODEL}",
                                re.IGNORECASE)
_SMALL = re.compile(r"\bsmall(?:er)?\s+(?:calls?|requests?|ones)\b", re.IGNORECASE)
_STOP = frozenset({"we", "it", "the", "all", "them", "this", "that", "model", "models"})


@dataclass(frozen=True)
class Ask:
    new: str
    old: str
    small_only: bool


def parse(text: str) -> Ask | None:
    t = text or ""
    for pattern, new_first in ((_SWAP_OLD_WITH_NEW, False), (_USE_NEW_INSTEAD, True), (_NEW_REPLACES_OLD, True)):
        m = pattern.search(t)
        if m and not {m.group(1).casefold(), m.group(2).casefold()} & _STOP:
            new, old = (m.group(1), m.group(2)) if new_first else (m.group(2), m.group(1))
            return Ask(prices.norm_model(new), prices.norm_model(old), bool(_SMALL.search(t)))
    return None


def _matches(models: pd.Series, name: str) -> pd.Series:
    norm = models.astype(str).map(prices.norm_model)
    return (norm == name) | norm.str.startswith(name + "-")


def compute(frame: Frame, ask: Ask) -> dict:
    df = frame.df
    table = prices.table_with(frame.meta.get("price_table"))
    base = {"label": "what-if (recomputed in code from the price table; not applied to the analysis)",
            "from": ask.old, "to": ask.new, "scope": "small calls only" if ask.small_only else "all calls",
            "price_as_of": prices.AS_OF}
    if "model" not in frame.dims:
        return {**base, "error": "this export has no model column, so a model swap can't be priced"}
    new_price = prices.lookup(ask.new, table)
    if new_price is None:
        return {**base, "error": f"no price for {ask.new}; add a price_table line for it"}
    rows = df[_matches(df["model"], ask.old)]
    if ask.small_only and not rows.empty:
        keys = unit_keys(frame)
        g = rows.groupby(keys)[["input", "output", "requests"]].transform("sum")
        small = (g["input"] / g["requests"] < T04_MAX_AVG_INPUT) & (g["output"] / g["requests"] < T04_MAX_AVG_OUTPUT)
        rows = rows[small]
    if rows.empty:
        return {**base, "error": f"no {'small ' if ask.small_only else ''}calls on {ask.old} in this export"}
    was = float(rows["cost"].sum())
    now = float(prices.token_cost(new_price, rows["input"], rows["output"], rows["cached_in"],
                                  rows["cache_write"]).sum())
    p = period(df)
    days = p["days"] if p else None
    return {**base, "rows": len(rows), "requests": float(rows["requests"].sum()), "base_cost": was,
            "new_cost": now, "saving": was - now, "saving_pct": (was - now) / was * 100 if was else None,
            "monthly_saving": monthly(was - now, days)}


def render(w: dict) -> str:
    head = f"**What-if: {w['to']} instead of {w['from']} ({w['scope']})**"
    if w.get("error"):
        return f"{head}\n{w['error']}."
    line = (f"{head}\n{fmt.count(w['requests'])} requests cost {fmt.money(w['base_cost'])} in this export; at "
            f"{w['to']} list prices they would cost {fmt.money(w['new_cost'])}: {fmt.money(w['saving'])} less "
            f"({fmt.pct(w['saving_pct'])}).")
    if w.get("monthly_saving") is not None:
        line += f" About {fmt.money(w['monthly_saving'])}/month at this run rate."
    return (line + f"\n_{w['label']}; prices as of {w['price_as_of']}; assumes {w['to']}'s quality is acceptable, "
            "test first._")
