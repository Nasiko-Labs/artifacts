"""Pure: exact, pre-formatted strings for the model. The model copies these and never computes; checks.py accepts
a number in model text only when it appears in one of these strings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import fmt

DIGEST_TOP = 5
MAX_FINDINGS = 10


def period_text(p: Mapping[str, Any] | None) -> str:
    return f"{p['start']} to {p['end']} ({p['days']} days)" if p else "no dates in this export"


def estimate_text(f: Mapping[str, Any]) -> str:
    if f.get("est_monthly_waste") is not None:
        return f"~{fmt.money(f['est_monthly_waste'])}/month"
    if f.get("est_in_export") is not None:
        return f"~{fmt.money(f['est_in_export'])} in this export"
    return "not quantified"


def _table(rows: list[dict]) -> list[dict]:
    return [{"key": r["key"], "cost": fmt.money(r["cost"]), "share": fmt.pct(r["share_pct"]),
             "requests": fmt.count(r["requests"])} for r in rows[:DIGEST_TOP]]


def build(result: Mapping[str, Any]) -> dict:
    t = result["totals"]
    out: dict[str, Any] = {
        "period": period_text(result["period"]),
        "totals": {"cost": fmt.money(t["cost"]), "requests": fmt.count(t["requests"]),
                   "tokens": fmt.count(t["input_tokens"] + t["output_tokens"]),
                   "cached_share": fmt.pct(t["cached_share_pct"]),
                   "cost_basis": ", ".join(f"{k}: {v} rows" for k, v in sorted(t["cost_basis_mix"].items()))},
        "by_model": _table(result["by_model"]),
        f"by_{result['team_label']}": _table(result["by_team"]),
        "by_task": _table(result["by_task"]),
        "findings": [{"id": f["id"], "rule": f["rule_id"], "title": f["title"], "where": f["key"],
                      "evidence": f["evidence"], "estimate": estimate_text(f), "assumption": f["assumption"]}
                     for f in result["waste"][:MAX_FINDINGS]],
        "not_computed": list(result["not_computed"]) + list(result["not_checked"]),
    }
    return out


def finding_ids(d: Mapping[str, Any]) -> set[str]:
    return {f["id"] for f in d.get("findings", [])}


def strings(value: Any) -> list[str]:
    """Every string in the digest (keys excluded)."""
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return [str(value)] if value is not None else []
