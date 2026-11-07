"""Pure: the stored result and everything derived from it. A finished run and every zero-call follow-up
("enterprise only", "sort by customers", "show quotes for theme 2") go through `assemble`, so a re-render
recomputes every number from the stored items instead of trusting an earlier answer."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import caveats
import churn
import filters
import metrics
import quotes
import render
from filters import Focus
from items import FeedbackItem


@dataclass(frozen=True)
class View:
    sort: str = "weight"
    top: int = 0
    only_churn: bool = False
    quotes: str = ""          # "" (one per theme) | "all" | a theme number
    items_for: int = 0        # a theme number whose items are listed
    as_json: bool = False


@dataclass(frozen=True)
class Stored:
    items: tuple[FeedbackItem, ...]
    themes: tuple[dict, ...]
    theme_of: dict[str, str]
    quote_ids: dict[str, list[str]] = field(default_factory=dict)
    churn: tuple[dict, ...] = ()
    run: dict = field(default_factory=dict)
    focus: Focus = field(default_factory=Focus)

    def to_state(self) -> dict:
        return {"items": [i.to_dict() for i in self.items], "themes": list(self.themes), "theme_of": self.theme_of,
                "quote_ids": self.quote_ids, "churn": list(self.churn), "run": self.run, "focus": self.focus.to_dict()}

    @staticmethod
    def from_state(data: dict | None) -> Stored | None:
        if not data or not data.get("items"):
            return None
        return Stored(tuple(FeedbackItem.from_dict(i) for i in data["items"]), tuple(data.get("themes") or ()),
                      dict(data.get("theme_of") or {}), dict(data.get("quote_ids") or {}),
                      tuple(data.get("churn") or ()), dict(data.get("run") or {}), Focus.from_dict(data.get("focus")))

    def with_focus(self, focus: Focus) -> Stored:
        return replace(self, focus=focus)


@dataclass(frozen=True)
class Report:
    text: str
    envelope: dict


def assemble(stored: Stored, view: View, today: date) -> Report:
    items = filters.apply(stored.items, stored.focus, today)
    stats, data = metrics.compute(items, stored.themes, stored.theme_of)
    if stored.focus.types:
        stats = [s for s in stats if s.type in stored.focus.types]
    stats = metrics.order(stats, view.sort)
    shown = [s for s in stats if s.shown]
    by_id = {i.id: i for i in items}
    quoted = {s.id: quotes.quotes_for(s, stored.quote_ids.get(s.id, ()), by_id) for s in shown}
    signals = churn.signals(stored.churn, items)
    kept = {i.id for i in items}
    run = dict(stored.run, unreached=[i for i in stored.run.get("unreached") or () if i in kept])
    focus_text = stored.focus.describe()
    notes = caveats.build(data, run, stats, len(stored.items), focus_text)
    conf = confidence(data, run)
    human = needs_human(conf, signals, data, run)
    envelope = build_envelope(shown, quoted, signals, notes, data, conf, human)
    body = render.render_json(envelope) if view.as_json else render.markdown(shown, quoted, signals, notes, data, view,
                                                                             by_id)
    return Report(body, envelope)


def confidence(data: metrics.Dataset, run: dict[str, Any]) -> str:
    share = data.clustered / data.items if data.items else 0.0
    if data.items < 20 or share < 0.4 or run.get("cut"):
        return "low"
    if data.items >= 50 and share >= 0.7 and data.customers and data.customers_with_arr * 2 >= data.customers:
        return "high"
    return "medium"


def needs_human(conf: str, signals: list[dict], data: metrics.Dataset, run: dict[str, Any]) -> bool:
    top = data.top_quartile_arr
    risky = top is not None and any(s["arr"] is not None and s["arr"] >= top for s in signals)
    return conf == "low" or risky or bool(run.get("column_default"))


def summary_line(shown: list[metrics.ThemeStats], data: metrics.Dataset) -> str:
    head = f"{data.items} items from {data.customers} customers: {len(shown)} themes"
    if not shown:
        return head + "; no theme reached three items or two customers"
    top = max(shown, key=lambda s: (s.weighted_score, s.count))
    return f"{head}; top by weight: {top.name} ({top.weighted_score:.1f}%)"


def build_envelope(shown: list[metrics.ThemeStats], quoted: dict[str, list[dict]], signals: list[dict],
                   notes: list[str], data: metrics.Dataset, conf: str, human: bool) -> dict:
    themes = [{"name": s.name, "type": s.type, "count": s.count, "customers": list(s.customers),
               "weighted_score": s.weighted_score, "trend": s.trend, "quotes": quoted.get(s.id, []),
               "suggested_next_step": s.next_step} for s in shown]
    churn_rows = [{k: v for k, v in s.items() if k != "item_id"} for s in signals]
    return {"summary": summary_line(shown, data), "items": themes, "themes": themes, "churn_signals": churn_rows,
            "caveats": notes, "confidence": conf, "needs_human": human}
