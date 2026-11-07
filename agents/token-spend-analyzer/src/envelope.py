"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields; `items`
mirrors `waste`). `confidence` and `needs_human` are computed here from the run's checks, never from the model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

LOW_DROPPED_SHARE = 0.20
LOW_UNPRICED_SHARE = 0.50
RUNAWAY_HUMAN_PCT = 70.0
FINDING_FIELDS = ("id", "rule_id", "title", "dimension", "key", "evidence", "est_monthly_waste", "est_in_export",
                  "assumption")


def confidence(result: Mapping[str, Any]) -> str:
    read = max(int(result["rows_read"]), 1)
    used = max(int(result["rows_used"]), 1)
    if result["rows_dropped"] / read > LOW_DROPPED_SHARE or result["unpriced_rows"] / used > LOW_UNPRICED_SHARE:
        return "low"
    if (result["computed_rows"] or result["not_computed"] or result["rec_source"] != "model"
            or result["recs_dropped"] or result["rows_dropped"] or result["capped"]):
        return "medium"
    return "high"


def runaway_share(result: Mapping[str, Any]) -> float:
    return max((float(f.get("share_pct") or 0.0) for f in result["waste"]
                if f["rule_id"] == "T06" and f["title"] == "runaway entity"), default=0.0)


def needs_human(result: Mapping[str, Any], level: str) -> bool:
    return level == "low" or runaway_share(result) > RUNAWAY_HUMAN_PCT


def build(result: Mapping[str, Any], what_if: Mapping[str, Any] | None = None) -> dict:
    level = confidence(result)
    findings = [{k: f.get(k) for k in FINDING_FIELDS} for f in result["waste"]]
    data = {
        "summary": result["summary"], "items": findings, "confidence": level,
        "needs_human": needs_human(result, level), "by_model": result["by_model"], "by_team": result["by_team"],
        "by_task": result["by_task"], "waste": findings, "totals": result["totals"], "period": result["period"],
        "recommendations": result["recommendations"], "not_computed": result["not_computed"],
        "not_checked": result["not_checked"], "team_dimension": result["team_label"], "shape": result["shape"],
        "cross": result["cross"], "change": result["change"], "rows_read": result["rows_read"],
        "rows_dropped": result["rows_dropped"], "items_dropped": result["recs_dropped"],
        "price_as_of": result["price_as_of"],
    }
    if what_if:
        data["what_if"] = dict(what_if)
    return data
