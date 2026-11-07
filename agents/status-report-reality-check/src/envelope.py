"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

import rules
import statuses as st
from match import EXPLICIT, TITLE

HIGH_MATCHED = 0.8
HIGH_DATES = 0.9
LOW_MATCHED = 0.5
LOW_UNKNOWN = 0.25
DATE_FIELDS = ("created", "updated", "due", "resolved")
_INTERNAL = ("order",)


def confidence(items: list[dict], tickets: list[dict], ctx: rules.Ctx, fallback: bool) -> str:
    matched = sum(1 for i in items if i["match"]["kind"] in (EXPLICIT, TITLE)) / len(items) if items else 0.0
    unknown = sum(1 for t in tickets if rules.category(t, ctx) == st.UNKNOWN) / len(tickets) if tickets else 1.0
    if matched < LOW_MATCHED or unknown > LOW_UNKNOWN or fallback:
        return "low"
    dated = [t for t in tickets if t.get("unparsed") or any(t.get(f) for f in DATE_FIELDS)]
    readable = sum(1 for t in dated if not t.get("unparsed")) / len(dated) if dated else 0.0
    return "high" if matched >= HIGH_MATCHED and readable >= HIGH_DATES else "medium"


def summary(result: dict) -> str:
    found = result["disagreements"]
    high = sum(1 for d in found if d["risk"] == rules.HIGH)
    text = (f"Compared {result['report_items']} report item{'s' if result['report_items'] != 1 else ''} with "
            f"{result['tickets']} ticket{'s' if result['tickets'] != 1 else ''}: {len(found)} "
            f"disagreement{'s' if len(found) != 1 else ''} ({high} high)")
    return text + f", {len(result['unmatched'])} unmatched"


def build(result: dict, items: list[dict], tickets: list[dict], ctx: rules.Ctx, meta: dict,
          notes: list[str]) -> dict:
    level = confidence(items, tickets, ctx, bool(meta.get("fallback")))
    found = [{k: v for k, v in d.items() if k not in _INTERNAL} for d in result["disagreements"]]
    env = {"summary": summary(result), "items": found, "disagreements": found, "unmatched": result["unmatched"],
           "caveats": notes, "confidence": level,
           "needs_human": level == "low" or any(d["risk"] == rules.HIGH for d in found),
           "as_of": result["as_of"], "stale_days": result["stale_days"], "soon_days": result["soon_days"],
           "report_items": result["report_items"], "tickets": result["tickets"]}
    if "unreported_risks" in result:
        env["unreported_risks"] = result["unreported_risks"]
    return env
