"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `themes`, `by_version`,
`competitor_comparison`, `caveats`, ...). `confidence` and `needs_human` are computed here from the run's
checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

LOW_DROP_SHARE = 0.2
REGRESSION = "regression suspected"
MIN_REVIEWS_FOR_HIGH = 30


def confidence(c: Mapping[str, Any]) -> str:
    returned = max(c["returned"], 1)
    if not c["analysed"] or c["salvage"] or c["dropped"] / returned > LOW_DROP_SHARE:
        return "low"
    if c["coverage_gap"] or c["capped"] or c["dropped"] or c["not_analysed"] or c["paste_cut"] \
            or c["parsed"] < MIN_REVIEWS_FOR_HIGH:
        return "medium"
    return "high"


def flagged(result: Mapping[str, Any]) -> list[str]:
    return [row["version"] for row in result.get("by_version", ()) if (row.get("flag") or "").startswith(REGRESSION)]


def summary(result: Mapping[str, Any]) -> str:
    o = result["overview"]
    avg = f", average rating {o['avg_rating']:g}" if o["avg_rating"] is not None else ""
    head = f"{o['app']}: {o['reviews']} reviews analysed{avg}; {len(_ours(result))} complaint themes."
    versions = flagged(result)
    if versions:
        head += f" Regression suspected in {', '.join(versions)}."
    if result.get("competitors"):
        head += f" Compared with {', '.join(c['app'] for c in result['competitors'])}."
    return head


def _ours(result: Mapping[str, Any]) -> list[dict]:
    return [t for t in result["themes"] if t["app"] == result["ours"]]


def build(result: Mapping[str, Any], checks: Mapping[str, Any], caveats: list[str]) -> dict:
    level = confidence(checks)
    return {"summary": summary(result), "items": list(result["themes"]), "confidence": level,
            "needs_human": level == "low" or bool(flagged(result)), **result, "caveats": list(caveats),
            "checks": dict(checks)}


def looks_like(data: Any) -> bool:
    """An envelope from an earlier run of this agent (accepted as input for follow-ups)."""
    required = ("summary", "confidence", "needs_human", "ours", "by_version", "caveats", "checks")
    return isinstance(data, Mapping) and isinstance(data.get("items"), list) and all(k in data for k in required) \
        and isinstance(data.get("overview"), Mapping) and isinstance(data.get("themes"), list)
