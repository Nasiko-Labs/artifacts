"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields), built
from the stored review state. `confidence` and `needs_human` are computed here, never taken from the model."""

from __future__ import annotations

from review import INTENT_FAILED_NOTE, verdict_of


def confidence(state: dict) -> str:
    if state["hard"]:
        return "low"
    if state["environment_source"] == "assumed" or state["fell_back"] or state["intent_failed"]:
        return "medium"
    return "high"


def needs_human(state: dict, verdict: str, level: str) -> bool:
    high = any(f["severity"] == "high" for f in state["findings"])
    return verdict == "block" or (verdict == "review" and high) or level == "low"


def template_summary(state: dict, verdict: str) -> str:
    """The code-written summary: used when the model's text failed its checks, and after a confirmation."""
    counts = state["counts"]
    high = [f for f in state["findings"] if f["severity"] == "high"]
    medium = [f for f in state["findings"] if f["severity"] == "medium"]
    if not state["has_changes"]:
        return "No changes: the plan does nothing."
    text = (f"Verdict {verdict.upper()}: {len(high)} high-risk and {len(medium)} medium-risk finding(s) across "
            f"{counts['total']} change(s).")
    blocking = [f["address"] for f in high if f["blocking"] and not f["confirmed"]]
    if blocking:
        text += f" Blocked until confirmed: {', '.join(dict.fromkeys(blocking))}."
    elif high:
        text += f" Top risk: {high[0]['address']}: {high[0]['reason']}"
    return text


def summary_of(state: dict, verdict: str) -> str:
    return state["summary"] or template_summary(state, verdict)


def questions(state: dict) -> list[str]:
    return [f"{f['address']}: {f['question']}" for f in state["findings"] if f["severity"] in ("high", "medium")]


def caveats(state: dict) -> list[str]:
    extra = [INTENT_FAILED_NOTE] if state["intent_failed"] else []
    if state["environment_source"] == "assumed":
        extra.append("Environment not stated, treated as prod.")
    return [*state["caveats"], *extra]


def build(state: dict) -> dict:
    verdict = verdict_of(state)
    level = confidence(state)
    high = [f for f in state["findings"] if f["severity"] == "high"]
    medium = [f for f in state["findings"] if f["severity"] == "medium"]
    return {"summary": summary_of(state, verdict), "items": high + medium, "confidence": level,
            "needs_human": needs_human(state, verdict, level), "verdict": verdict, "counts": state["counts"],
            "high_risk": high, "medium_risk": medium,
            "low_risk": [f for f in state["findings"] if f["severity"] == "low"],
            "questions_to_ask": questions(state), "suggested_apply_order": state["apply_order"],
            "environment": state["environment"], "environment_assumed": state["environment_source"] == "assumed",
            "format": state["format"], "intent": state["intent"], "caveats": caveats(state), "notes": state["notes"],
            "drift": state["drift"],
            "confirmed": list(dict.fromkeys(f["address"] for f in state["findings"] if f["confirmed"]))}
