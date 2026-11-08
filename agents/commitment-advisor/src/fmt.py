"""Pure: number formatting, the facts digest the model words its explanation from, and the allow-list of
numbers the explanation may contain (every number in the digest, compared by value)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
SMALL_INT_MAX = 10
ACTION_MEANING = {
    "renew_more": "renew it at a higher level (do not let it lapse)",
    "renew_same": "renew it at the same level (do not let it lapse)",
    "renew_less": "renew it at a lower level (do not let it lapse)",
    "let_lapse": "let it lapse",
    "underused": "keep it (it cannot be cancelled) but it is underused",
    "keep": "keep it",
}


def money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def days(value: float) -> str:
    return f"{value:g}" if value == int(value) else f"{value:.1f}"


def _term_facts(s: Mapping[str, Any]) -> dict:
    if not s.get("eligible"):
        return {"term": f"{s['term_years']}y", "eligible": "no"}
    return {"term": f"{s['term_years']}y", "hourly_commit": money(s["hourly_commit"]),
            "monthly_saving": money(s["monthly_saving"]), "term_saving": money(s["term_saving"]),
            "utilization": pct(s["utilization"]), "downside_monthly_saving": money(s["downside_monthly_saving"]),
            "downside_utilization": pct(s["downside_utilization"]), "risk": s["risk"]}


def rec_facts(r: Mapping[str, Any]) -> dict:
    out = {"scope": r["scope"], "action": r["action"], "confidence": r["confidence"]}
    if r["action"] == "buy_now":
        out |= {"recommended_term": f"{r['term_years']}y", "hourly_commit": money(r["hourly_commit"]),
                "monthly_saving": money(r["monthly_saving"]), "term_saving": money(r["term_saving"]),
                "utilization": pct(r["utilization"]), "break_even_utilization": pct(r["break_even_utilization"]),
                "coverage": pct(r["coverage_pct"]), "downside_monthly_saving": money(r["downside_monthly_saving"]),
                "downside_utilization": pct(r["downside_utilization"]), "risk": r["risk"]}
    elif r.get("reason"):
        out["reason"] = r["reason"]
    if "existing_od_hourly" in r:
        out["existing_cover_od_hourly"] = money(r["existing_od_hourly"])
    if r.get("terms"):
        out["new_purchase_terms"] = [_term_facts(s) for s in r["terms"].values()]
    return out


def existing_facts(e: Mapping[str, Any]) -> dict:
    out = {"id": e["id"], "type": e["type"], "action": e["action"], "action_meaning": ACTION_MEANING[e["action"]],
           "hourly_commit": money(e["hourly_commit"]),
           "ends_in_days": str(e["ends_in_days"]), "end_date": e["end_date"]}
    if e.get("target_hourly_commit") is not None:
        out["target_hourly_commit"] = money(e["target_hourly_commit"])
    if e.get("portfolio_utilization") is not None:
        out["portfolio_utilization"] = pct(e["portfolio_utilization"])
    if e.get("wasted_per_month") is not None:
        out["wasted_per_month"] = money(e["wasted_per_month"])
    return out


def digest(result: Mapping[str, Any]) -> dict:
    c = result["coverage"]
    return {"data": f"{c['days']} days of {c['granularity']} data, {c['start']} to {c['end']}",
            "recommendations": [rec_facts(r) for r in result["recommendations"]],
            "existing_commitments": [existing_facts(e) for e in result["existing"]],
            "assumptions": list(result["assumptions"]), "notes": list(result.get("notes") or [])}


def strings(data: Any) -> list[str]:
    if isinstance(data, Mapping):
        return [s for v in data.values() for s in strings(v)]
    if isinstance(data, list | tuple):
        return [s for v in data for s in strings(v)]
    return [str(data)] if data is not None else []


def values_in(text: str) -> list[float]:
    return [float(m.group(0).replace(",", "")) for m in _NUMBER.finditer(text or "")]


def allowed(digest_: Mapping[str, Any]) -> set[float]:
    return {round(v, 2) for s in strings(digest_) for v in values_in(s)}


def ungrounded(text: str, allow: set[float]) -> list[float]:
    """Numbers in `text` that are not in the digest (small whole numbers like "2 sentences" are fine)."""
    return [v for v in values_in(text) if round(v, 2) not in allow and not (v == int(v) and v <= SMALL_INT_MAX)]
