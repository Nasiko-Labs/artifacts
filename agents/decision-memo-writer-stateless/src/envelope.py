"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

import criteria as crit
from brief import Brief
from memo import names

HIGH_MAX_DROPPED = 0.10
LOW_MIN_DROPPED = 0.30
MIN_TRIGGERS = 3


def dropped_share(analysis: dict, memo: dict) -> float:
    total = analysis["total"] + memo["total"]
    return (analysis["dropped"] + memo["dropped"]) / total if total else 0.0


def confidence(brief: Brief, analysis: dict, memo: dict, basis: dict, open_items: list[str]) -> str:
    share = dropped_share(analysis, memo)
    if brief.user_options < 2 or share > LOW_MIN_DROPPED or memo["repair_failed"]:
        return "low"
    if (crit.is_weighted(brief.criteria) and not memo["overridden"] and len(memo["triggers"]) >= MIN_TRIGGERS
            and share < HIGH_MAX_DROPPED and not basis["close_call"] and not open_items):
        return "high"
    return "medium"


def build(brief: Brief, analysis: dict, memo: dict, basis: dict, markdown: str, open_items: list[str]) -> dict:
    n, rec = names(brief), basis["option"]
    level = confidence(brief, analysis, memo, basis, open_items)
    scores = [{"option": k, "score": basis["scores"][k]} for k in basis["ranked"]]
    items = [{"option": o.key, "name": o.name, "score": basis["scores"].get(o.key), "added_by_me": o.added_by_me,
              "pros": [p["text"] for p in analysis["pros"][o.key]], "cons": [c["text"] for c in analysis["cons"][o.key]]}
             for o in brief.options]
    summary = f"Recommend {rec} ({n.get(rec, rec)})"
    if basis["scores"] and basis["runner_up"]:
        summary += f", {basis['scores'][rec]} vs {basis['runner_up']} {basis['scores'][basis['runner_up']]}"
    summary += {"override": ", your choice", "preference": ", your stated preference",
                "constraints": ", weighed against your constraints", "criteria": ", on your criteria"}[basis["kind"]]
    summary += " (close call)." if basis["close_call"] else "."
    return {"summary": summary, "items": items, "memo_markdown": markdown,
            "recommendation": {"option": rec, "name": n.get(rec, rec), "basis": basis["kind"],
                               "text": memo["recommendation"], "close_call": basis["close_call"]},
            "reversal_triggers": [{"trigger": t["trigger"], "would_switch_to": t["would_switch_to"]}
                                  for t in memo["triggers"]],
            "scores": scores, "risks": analysis["risks"], "open_items": open_items,
            "confidence": level, "needs_human": level == "low" or basis["close_call"] or bool(open_items)}
