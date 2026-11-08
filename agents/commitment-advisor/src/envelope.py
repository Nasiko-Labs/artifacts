"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `recommendations`,
`assumptions`, `coverage`). `confidence` and `needs_human` are computed here, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import fmt
from constants import NEEDS_HUMAN_TERM_SAVING

LEVELS = ("low", "medium", "high")


def confidence(result: Mapping[str, Any]) -> str:
    found = [r["confidence"] for r in result["recommendations"]] or ["low"]
    return min(found, key=LEVELS.index)


def needs_human(result: Mapping[str, Any]) -> bool:
    recs = result["recommendations"]
    return (result.get("needs_more_data", False)
            or any(r.get("risk") == "high" for r in recs)
            or any((r.get("term_saving") or 0) >= NEEDS_HUMAN_TERM_SAVING for r in recs)
            or (not result["quotes"] and any(r.get("term_years") == 3 for r in recs)))


def summary(result: Mapping[str, Any]) -> str:
    if result.get("needs_more_data"):
        return "Not enough usage data: at least 14 days are needed."
    parts = []
    for r in result["recommendations"]:
        if r["action"] == "buy_now":
            parts.append(f"{r['scope']}: buy {r['term_years']}y at {fmt.money(r['hourly_commit'])} $/hr, saves "
                         f"about {fmt.money(r['monthly_saving'])}/month (risk {r['risk']}).")
        else:
            parts.append(f"{r['scope']}: no commitment recommended.")
    parts += [f"{e['id']}: {e['action']}." for e in result["existing"] if e["action"] != "keep"]
    return " ".join(parts)


def build(result: Mapping[str, Any]) -> dict:
    recs = list(result["recommendations"])
    return {"summary": summary(result), "items": recs, "confidence": confidence(result),
            "needs_human": needs_human(result), "recommendations": recs,
            "existing_commitments": list(result["existing"]), "assumptions": list(result["assumptions"]),
            "coverage": dict(result["coverage"]), "notes": list(result.get("notes") or []),
            "explanation": result.get("explanation"), "explanation_source": result.get("explanation_source")}
