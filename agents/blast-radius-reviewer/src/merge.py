"""Merge: fired probes + checked judgements -> at-risk items, cleared items and the envelope (pure), plus the one
tool-less call that writes `one_fact` from the merged items."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import checks
import llm
import prompts
import severity

MAX_ITEMS = 15
MAX_FACT_WORDS = 40
FACT_PREFIX = "This is safe if "
NO_FACT = "No single safety fact identified; treat the change as not yet understood."


@dataclass(frozen=True)
class Merged:
    items: list[dict]
    cleared: list[dict]
    dropped: int
    replaced: int
    judged: int


def merge(fired: Sequence[dict], judgements: dict[str, dict], diff_text: str, context: str) -> Merged:
    diff_norm, context_norm = checks.normalise(diff_text), checks.normalise(context)
    items, cleared, seen = [], [], set()
    dropped = replaced = judged = 0
    for f in fired:
        key = (f["kind"], f["token"])
        if key in seen:
            continue
        seen.add(key)
        j = judgements.get(f["id"])
        v = checks.verify(f, j, diff_norm, context_norm)
        judged += j is not None
        dropped += v.dropped
        replaced += v.replaced
        if v.applies is False and v.context_ok:
            cleared.append({"id": f["id"], "kind": f["kind"], "token": f["token"], "file": f["file"],
                            "severity": severity.shift(f["severity"], -1),
                            "reason": f"context shows: \"{checks.normalise(v.context_quote)[:200]}\""})
            continue
        item_rung = severity.rung(v.diff_ok, v.context_ok)
        applies = "unjudged" if v.applies is None else ("yes" if v.applies is True else "unclear")
        items.append({"id": f["id"], "probe": f["probe"], "kind": f["kind"],
                      "severity": severity.item_severity(f["severity"], item_rung, v.applies), "rung": item_rung,
                      "file": f["file"], "line": f["line"], "token": f["token"], "what": v.what, "why": v.why,
                      "how_to_verify": list(f["commands"]), "safe_if": v.safe_if, "applies": applies,
                      "destructive": bool(f.get("destructive"))})
    order: dict[str, int] = {}
    for n, f in enumerate(fired):
        order.setdefault(f["file"], n)
    items.sort(key=lambda i: (-severity.LEVELS.index(i["severity"]), order.get(i["file"], 0), i["line"] or 0))
    return Merged(items[:MAX_ITEMS], cleared, dropped, replaced, judged)


def check_fact(text: str, input_norm: str) -> str:
    fact = " ".join((text or "").split())
    if not fact.startswith(FACT_PREFIX) or len(fact.split()) > MAX_FACT_WORDS:
        return ""
    if checks.claims_safety(fact) or not checks.quoted_tokens_ok(fact, input_norm) or \
            checks.unknown_places(fact, input_norm):
        return ""
    return checks.clean(fact, input_norm)


async def one_fact(deps: llm.TurnDeps, items: Sequence[dict], diff_text: str, context: str,
                   policy: Sequence[str]) -> tuple[str, bool]:
    """(the fact or NO_FACT, whether a model call failed). Skipped when no item is at risk."""
    if not items:
        return "", False
    if not deps.has_time() or deps.budget.calls >= deps.budget.max_calls:
        return NO_FACT, False

    def validate(answer):
        return answer if isinstance(answer, dict) and isinstance(answer.get("one_fact"), str) else None

    try:
        answer = await llm.complete_json(deps, prompts.one_fact_messages(items, policy), prompts.ONE_FACT_SYSTEM,
                                         validate, max_tokens=200)
    except llm.LlmError:
        return NO_FACT, True
    input_norm = checks.normalise(f"{diff_text}\n{context}")
    return check_fact(answer["one_fact"], input_norm) or NO_FACT, False


def summary(items: Sequence[dict], cleared: Sequence[dict], fired_count: int) -> str:
    if not items:
        if fired_count and cleared:
            return f"Nothing found in what you pasted: {len(cleared)} flagged place(s) cleared by your context."
        return "Nothing found in what you pasted." if fired_count else \
            "No risk pattern fired in what you pasted."
    high = sum(i["severity"] == "high" for i in items)
    text = f"{len(items)} risk(s) outside the diff ({high} high)."
    if high:
        text += " Nothing pasted proves the high one(s) safe."
    return text


def envelope(m: Merged, *, fact: str, fired_count: int, coverage: dict, repaired: bool, context_given: bool,
             intent: str = "") -> dict:
    v = severity.verdict(m.items)
    level = severity.confidence(m.items, fired=fired_count, judged=m.judged, dropped=m.dropped, repaired=repaired,
                                truncated=bool(coverage.get("skipped")), context_given=context_given)
    at_risk = [i for i in m.items if i["applies"] != "unclear"]
    coverage = {**coverage, "dropped_quotes": m.dropped, "generic_items": sum(i["rung"] == "inferred" for i in m.items),
                "replaced_sentences": m.replaced, "context_given": context_given}
    return {"summary": summary(m.items, m.cleared, fired_count), "items": m.items, "at_risk": at_risk,
            "cleared": m.cleared, "one_fact": fact, "verdict": v, "verdict_text": severity.VERDICT_WORDS[v],
            "confidence": level, "needs_human": severity.needs_human(v, level), "intent": intent,
            "coverage": coverage}


def tokens_in(text: str, fired: Sequence[dict]) -> list[str]:
    """Ids of fired probes whose token appears in `text` (a pasted grep output or caller code)."""
    folded = (text or "").casefold()
    return [f["id"] for f in fired
            if f["token"] and re.search(rf"(?<!\w){re.escape(f['token'].casefold())}(?!\w)", folded)]
