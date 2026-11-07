"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import digest as dg

LOW_DROPPED_SHARE = 0.10
MEDIUM_EMPTY_SHARE = 0.01
BUDGET_TRIGGER_PCT = 10.0


def confidence(result: Mapping[str, Any]) -> str:
    rows = max(int(result["rows_read"]), 1)
    if result["guessed"] or result["rows_dropped"] / rows > LOW_DROPPED_SHARE or not result["window"]["comparable"]:
        return "low"
    if (result["narrative_source"] != "model" or result["empty_costs"] / rows > MEDIUM_EMPTY_SHARE or result["cut"]
            or result["dropped"]):
        return "medium"
    return "high"


def needs_human(result: Mapping[str, Any], level: str) -> bool:
    budget = result["totals"].get("budget") or {}
    return level == "low" or float(budget.get("projected_vs_budget_pct") or 0.0) > BUDGET_TRIGGER_PCT


def summary(result: Mapping[str, Any]) -> str:
    d = result["digest"]
    head = f"{d['period']}: gross {d['totals']['gross']}, net {d['totals']['net']}."
    if d["top_movers"]:
        m = d["top_movers"][0]
        head += f" Top mover: {m['name']} {m['change']} ({m['change_pct']})."
    if d["anomalies"]:
        head += f" {len(d['anomalies'])} anomal{'y' if len(d['anomalies']) == 1 else 'ies'}."
    return head


def build(result: Mapping[str, Any]) -> dict:
    level = confidence(result)
    totals = {k: result["totals"].get(k) for k in ("gross", "credits", "net", "daily_avg", "currency",
                                                    "untagged_share_pct")}
    if result["totals"].get("budget"):
        totals["budget"] = result["totals"]["budget"]
    movers = [{"name": m["name"], "dimension": m["dimension"], "prev": m["prev"], "cur": m["cur"],
               "change_abs": m["change_abs"], "change_pct": m["change_pct"], "likely_cause": m["likely_cause"] or None,
               "evidence": m["evidence"]} for m in result["top_movers"]]
    return {
        "summary": summary(result), "items": movers, "confidence": level, "needs_human": needs_human(result, level),
        "period": {"start": result["period"]["start"], "end": result["period"]["end"], "days": result["period"]["days"]},
        "comparison": result["window"]["label"] or None, "totals": totals, "top_movers": movers,
        "account_movers": result["account_movers"], "anomalies": result["anomalies"],
        "recommendations": result["recommendations"], "narrative": result["narrative"],
        "waste_patterns": result["waste_patterns"], "untagged": result["untagged"], "chart": result["chart"],
        "chart_data": result["chart_data"], "not_computed": result["not_computed"], "rows_read": result["rows_read"],
        "rows_dropped": result["rows_dropped"], "items_dropped": result["dropped"],
        "currency_symbol": dg.symbol(result["totals"].get("currency")).strip(),
    }
