"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `actionable`, `noise`,
`tune_suggestions`, `unclear`, `totals`, `period`, `not_computed`). `confidence` and `needs_human` are computed
here from the run's checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

LOW_PARSE_FAIL = 0.2
LOW_MISSING_DURATIONS = 0.5
LOW_MIN_ROWS = 10
MEDIUM_UNCLEAR_SHARE = 0.3
UNCLEAR_PAGES_TRIGGER = 5


def confidence(result: Mapping[str, Any]) -> str:
    c, t = result["checks"], result["totals"]
    seen = max(c["rows_seen"], 1)
    fires = max(t["fires"], 1)
    if c["rows_skipped"] / seen > LOW_PARSE_FAIL or c["durations_missing"] / fires > LOW_MISSING_DURATIONS \
            or t["fires"] < LOW_MIN_ROWS:
        return "low"
    if c["notes_by_model"] or c["notes_unlabelled"] or c["dropped"] or c["rows_skipped"] \
            or t["unclear_fires"] / fires > MEDIUM_UNCLEAR_SHARE:
        return "medium"
    return "high"


def needs_human(result: Mapping[str, Any], level: str) -> bool:
    return level == "low" or any(g["label"] == "unclear" and g["signals"]["pages"] >= UNCLEAR_PAGES_TRIGGER
                                 for g in result["groups"])


def item(g: Mapping[str, Any]) -> dict:
    return {"name": g["name"], "service": g["service"] or None, "count": g["signals"]["n"], "reason": g["reason"],
            "evidence": g["evidence"]}


def suggestion(s: Mapping[str, Any]) -> dict:
    return {"group_key": s["group_key"], "name": s["name"], "change_type": s["change_type"], "text": s["text"],
            "assumption": s["assumption"], "est_pages_saved": s["est_pages_saved"],
            "est_fires_saved": s["est_fires_saved"]}


def summary(result: Mapping[str, Any]) -> str:
    t, p = result["totals"], result["period"]
    head = (f"{t['fires']} fires over {p['days']} day{'s' if p['days'] != 1 else ''}, {t['groups']} distinct alerts: "
            f"{t['actionable']} actionable, {t['noise']} noise ({_pct(t['noise_share_pct'])} of fires), "
            f"{t['unclear']} unclear.")
    if t["pages_saved_if_applied"]:
        head += (f" The tuning suggestions would have avoided {t['pages_saved_if_applied']} of "
                 f"{t['pages_before']} pages.")
    return head


def _pct(value: float) -> str:
    return f"{value:g}%"


def build(result: Mapping[str, Any]) -> dict:
    level = confidence(result)
    actionable = [item(g) for g in result["groups"] if g["label"] == "actionable"]
    noise = [item(g) for g in result["groups"] if g["label"] == "noise"]
    unclear = [item(g) for g in result["groups"] if g["label"] == "unclear"]
    t = result["totals"]
    return {
        "summary": summary(result), "items": actionable + noise, "confidence": level,
        "needs_human": needs_human(result, level),
        "actionable": actionable, "noise": noise, "tune_suggestions": [suggestion(s) for s in result["tune"]],
        "unclear": unclear,
        "totals": {"fires": t["fires"], "groups": t["groups"], "noise_fires": t["noise_fires"],
                   "noise_share_pct": t["noise_share_pct"], "pages_before": t["pages_before"],
                   "pages_saved_if_applied": t["pages_saved_if_applied"]},
        "period": result["period"], "not_computed": result["not_computed"],
        "rows_read": result["checks"]["rows_seen"], "rows_skipped": result["checks"]["rows_skipped"],
        "items_dropped": result["checks"]["dropped"],
    }
