"""Pure: classify one group from its signals, in the plan's order, and fill the reason template with the
signal values. The model writes no reason text. Thresholds are the constants below (defaults tuned on
synthetic data; review them against one real week)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fires import fmt_dur

ACTION_SHARE = 0.3
LONG_SECS = 600
AUTO_LOW = 0.5
CRITICAL_ACK_NONE = 0.5
MIN_FIRES = 3
BLIP_SHORT = 0.8
BLIP_AUTO = 0.8
FLAP_MIN_FIRES = 5
ACK_NONE = 0.7
ACTIONABLE, NOISE, UNCLEAR = "actionable", "noise", "unclear"


def _ge(value: float | None, bar: float) -> bool:
    return value is not None and value >= bar


def actionable_rules(s: Mapping[str, Any]) -> list[str]:
    hits = []
    if _ge(s["action_share"], ACTION_SHARE):
        hits.append("action")
    if (s["median_dur"] or 0) >= LONG_SECS and (s["auto_resolved_share"] or 0) < AUTO_LOW and s["pages"] >= 1:
        hits.append("long")
    if s["escalated"] or (s["critical"] and (s["ack_no_action_share"] or 0) < CRITICAL_ACK_NONE):
        hits.append("escalated" if s["escalated"] else "critical")
    return hits


def noise_rules(s: Mapping[str, Any]) -> list[str]:
    explicit = s["n"] > 0 and s["no_action_notes"] == s["n"]
    if s["n"] < MIN_FIRES and not explicit:
        return []
    hits = []
    if _ge(s["short_share"], BLIP_SHORT) and _ge(s["auto_resolved_share"], BLIP_AUTO):
        hits.append("blip")
    if s["flap"] >= 1 and s["n"] >= FLAP_MIN_FIRES:
        hits.append("flap")
    if _ge(s["ack_no_action_share"], ACK_NONE):
        hits.append("ack_no_action")
    if s["periodic"] and not s["actions"]:
        hits.append("periodic")
    if s["storm_parent"] and not s["actions"]:
        hits.append("storm")
    return hits


def classify(s: Mapping[str, Any], too_few_rows: bool = False) -> tuple[str, tuple[str, ...], str]:
    """(label, rules that fired, reason)."""
    if too_few_rows:
        return UNCLEAR, (), f"not enough evidence: {_fires(s['n'])} in a paste of fewer than 5 fires"
    if s["n"] < MIN_FIRES and not s["noted"]:
        return UNCLEAR, (), f"not enough evidence: n={s['n']}, no notes"
    good, bad = actionable_rules(s), noise_rules(s)
    if good and bad:
        if _ge(s["action_share"], ACTION_SHARE):
            return ACTIONABLE, tuple(good), reason(s, ACTIONABLE, good)
        return UNCLEAR, tuple(good + bad), "conflicting signals: " + reason(s, UNCLEAR, good + bad)
    if good:
        return ACTIONABLE, tuple(good), reason(s, ACTIONABLE, good)
    if bad:
        return NOISE, tuple(bad), reason(s, NOISE, bad)
    detail = "no notes" if not s["noted"] else f"{s['noted']} with notes"
    return UNCLEAR, (), f"not enough evidence: n={s['n']}, {detail}; " + _base(s)


def _fires(n: int) -> str:
    return f"{n} fire" if n == 1 else f"{n} fires"


def _base(s: Mapping[str, Any]) -> str:
    days = s["span_days"]
    head = (f"{_fires(s['n'])} on {_date(s['first'])}" if days == 1 else f"{_fires(s['n'])} in {days} days")
    if s["median_dur"] is not None:
        head += f"; median {fmt_dur(s['median_dur'])}"
    return head


def _date(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d")


def _phrase(s: Mapping[str, Any], rule: str) -> str:
    return {
        "action": f"{s['actions']} of {s['n']} led to an action",
        "long": f"p90 {fmt_dur(s['p90_dur'])}, {s['pages']} paged, {s['auto_resolved']} auto-resolved",
        "escalated": "notes record an escalation or incident",
        "critical": f"critical severity, {s['ack_no_action']} of {s['touched']} acks/notes with no action",
        "blip": f"{s['short']} of {s['with_duration']} under 5 min; {s['auto_resolved']} auto-resolved with no ack",
        "flap": f"flapped in {s['flap']} episode{'s' if s['flap'] != 1 else ''} (3+ fires within 60 min)",
        "ack_no_action": f"{s['ack_no_action']} of {s['touched']} acked or noted with no action",
        "periodic": f"fires on a schedule, about every {fmt_dur(s['mean_gap_s'])}",
        "storm": (f"{round(s['storm_share'] * 100)}% of fires start within 5 min after {s['storm_parent']}"),
    }[rule]


def reason(s: Mapping[str, Any], label: str, rules: list[str]) -> str:
    parts = [_base(s), *(_phrase(s, r) for r in rules)]
    if label == NOISE and not s["actions"]:
        parts.append("none led to an action")
    return "; ".join(parts)
