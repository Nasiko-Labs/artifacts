"""Pure: per-item severity and evidence rung, the verdict, confidence and needs_human. All computed in code."""

from __future__ import annotations

from collections.abc import Sequence

LEVELS = ["low", "medium", "high"]
SEEN, POINTED, INFERRED = "seen_in_input", "pointed", "inferred"
VERDICT_WORDS = {"block": "block", "review": "review", "safe": "nothing found in what you pasted"}


def shift(level: str, by: int) -> str:
    return LEVELS[max(0, min(len(LEVELS) - 1, LEVELS.index(level) + by))]


def rung(diff_ok: bool, context_ok: bool) -> str:
    if diff_ok and context_ok:
        return SEEN
    return POINTED if diff_ok else INFERRED


def item_severity(default: str, item_rung: str, applies) -> str:
    """+1 level when the input shows the token used elsewhere and the model says the risk is real."""
    return shift(default, 1) if item_rung == SEEN and applies is True else default


def verdict(items: Sequence[dict]) -> str:
    if any(i["severity"] == "high" and i["rung"] == SEEN for i in items) or \
            any(i["probe"] == "R3" and i.get("destructive") for i in items):
        return "block"
    if any(i["severity"] in ("high", "medium") for i in items):
        return "review"
    return "safe"


def confidence(items: Sequence[dict], *, fired: int, judged: int, dropped: int, repaired: bool, truncated: bool,
               context_given: bool) -> str:
    if dropped or repaired or truncated or (fired and not judged):
        return "low"
    if any(i["rung"] == SEEN for i in items):
        return "high"
    if fired and judged == fired and context_given:
        return "high"
    return "medium"


def needs_human(verdict_value: str, confidence_value: str) -> bool:
    return verdict_value in ("block", "review") or confidence_value == "low"
