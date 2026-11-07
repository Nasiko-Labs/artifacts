"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields:
`estimated_monthly`, `violations`, `verdict`, ...). `items` mirrors `violations`. `confidence` and
`needs_human` are computed in verdict.py, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import verdict as vd

TOP_LINES = 15
VIOLATION_FIELDS = ("id", "rule_id", "severity", "title", "address", "attribute", "evidence", "est_monthly_saving",
                    "assumption")


def violations(result: Mapping[str, Any]) -> list[dict]:
    fixes = result.get("fixes") or {}
    return [{**{k: v[k] for k in VIOLATION_FIELDS}, "fix": fixes.get(v["id"]) or v["hint"]}
            for v in result["violations"]]


def build(result: Mapping[str, Any]) -> dict:
    level = vd.confidence(result)
    found = violations(result)
    lines = [{k: ln[k] for k in ("address", "type", "qty", "monthly", "basis", "detail", "assumptions", "change")}
             for ln in result["lines"][:TOP_LINES]]
    return {
        "summary": result["narrative"], "items": found, "confidence": level,
        "needs_human": vd.needs_human(result, level), "verdict": result["verdict"],
        "verdict_reasons": result["verdict_reasons"], "estimated_monthly": result["estimated_monthly"],
        "estimated_monthly_before": result["estimated_monthly_before"], "delta": result["delta"],
        "limit": result["limit"], "region": result["region"], "format": result["format"], "as_of": result["as_of"],
        "currency": "USD", "violations": found, "lines": lines, "assumptions": result["assumptions"],
        "unpriced": result["unpriced"], "usage_dependent": result["usage_dependent"],
        "savings_note": "potential, not additive", "items_dropped": result.get("dropped", 0),
        "notes": result["notes"],
    }
