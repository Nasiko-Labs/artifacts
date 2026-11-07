"""Pure: the envelope (the workflow-step contract) built from what the pull found. `confidence` and
`needs_human` are computed here from checks, never taken from the model; `summary` is built by code.
`items` and `themes` are the same list."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import caveats
import churn
import linking
import quotes
from caveats import RunFacts
from records import Item
from themes import Dataset, ThemeStats
from window import Window

DROP_SHARE = 0.2


@dataclass(frozen=True)
class Found:
    window: Window
    items: tuple[Item, ...]
    stats: tuple[ThemeStats, ...]
    data: Dataset
    labels_of: Mapping[str, Sequence[str]]
    quote_ids: Mapping[str, Sequence[str]]
    flagged: tuple[dict, ...]
    issues: list[dict] | None          # None: the tracker was not read
    run: RunFacts


def envelope(found: Found) -> dict:
    by_id = {i.id: i for i in found.items}
    shown = [s for s in found.stats if s.shown]
    themes = [_theme(s, found, by_id) for s in shown]
    signals = [{k: v for k, v in row.items() if k != "item_id"} for row in churn.signals(found.flagged, found.items)]
    lines = caveats.build(found.data, found.run, found.stats)
    confidence = compute_confidence(found.data, found.run)
    top = found.data.top_quartile_arr
    needs_human = (confidence == "low" or any(top is not None and (s["arr"] or 0) >= top for s in signals)
                   or found.run.default_channel or found.run.clamped)
    return {"summary": summary(found, themes), "items": themes, "themes": themes, "churn_signals": signals,
            "caveats": lines, "confidence": confidence, "needs_human": bool(needs_human),
            "window": {"start": found.window.start.isoformat(), "end": found.window.end.isoformat()},
            "sources": dict(found.run.read_counts)}


def _theme(stat: ThemeStats, found: Found, by_id: Mapping[str, Item]) -> dict:
    theme = {"name": stat.name, "type": stat.type, "count": stat.count, "customers": list(stat.customers),
             "distinct_customers": stat.distinct_customers, "unattributed": stat.unattributed,
             "weighted_score": stat.weighted_score, "trend": stat.trend,
             "quotes": quotes.quotes_for(stat, found.quote_ids.get(stat.id, ()), by_id),
             "suggested_next_step": stat.next_step}
    if found.issues is None:
        return {**theme, "linked_issues": [], "link_action": "Existing issues were not checked"}
    links = linking.links_for(stat, found.labels_of.get(stat.id, ()), found.issues)
    return {**theme, "linked_issues": [{"id": i.id, "title": i.title, "url": i.url, "state": i.state, "score": i.score,
                                        "closed": i.closed} for i in links],
            "link_action": linking.link_action(links)}


def summary(found: Found, themes: Sequence[dict]) -> str:
    head = (f"{caveats.plural(found.data.items, 'item')} from {caveats.plural(found.data.customers, 'customer')}: "
            f"{caveats.plural(len(themes), 'theme')}")
    if not themes:
        return head + "; no theme reached three items or two customers"
    return f"{head}; top by weight: {themes[0]['name']} ({themes[0]['weighted_score']:.1f}%)"


def compute_confidence(data: Dataset, run: RunFacts) -> str:
    clustered = data.clustered / data.items if data.items else 0.0
    if data.items < 20 or clustered < 0.4 or run.cut:
        level = "low"
    elif data.items >= 50 and clustered >= 0.7 and data.weighting == "arr":
        level = "high"
    else:
        level = "medium"
    dropped_share = run.dropped / run.extracted if run.extracted else 0.0
    if not run.sources_read or dropped_share > DROP_SHARE or run.salvage:
        return "low"
    source_missing = bool(run.notes) or not run.crm_read or not run.tracker_read
    if level == "high" and (source_missing or run.capped or run.dropped):
        return "medium"
    return level


_THEME_KEYS = ("name", "type", "count", "distinct_customers", "weighted_score", "suggested_next_step")


def adopt(env: Mapping) -> dict | None:
    """An envelope from a previous step, kept only with themes this agent can render; nothing is filled in."""
    raw = env.get("themes") or env.get("items") or []
    themes = [dict(t) for t in raw if isinstance(t, Mapping) and all(k in t for k in _THEME_KEYS)
              and isinstance(t["count"], int) and isinstance(t["weighted_score"], int | float)]
    if not themes:
        return None
    return {"summary": str(env.get("summary") or ""), "items": themes, "themes": themes,
            "churn_signals": [s for s in env.get("churn_signals") or [] if isinstance(s, Mapping)
                              and all(k in s for k in ("customer", "kind", "quote"))],
            "caveats": [str(c) for c in env.get("caveats") or []] + ["Taken from a previous step's result."],
            "confidence": env.get("confidence") if env.get("confidence") in ("high", "medium", "low") else "low",
            "needs_human": bool(env.get("needs_human", True)), "window": dict(env.get("window") or {}),
            "sources": {str(k): int(v) for k, v in dict(env.get("sources") or {}).items() if isinstance(v, int)}}
