"""Pure: the output envelope (the reply's DataPart, and the text part in json mode). `confidence` and
`needs_human` are computed here from the checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Sequence

import facts as fx
from compose import Composed

LOW_DROP_THRESHOLD = 2
KIND_LABELS = {"problem": "Problem", "customer": "Customer", "impact": "Impact", "attempted": "Tried",
               "status": "Status", "ask": "Ask", "deadline": "Deadline", "blocker": "Blocker",
               "stakeholder": "Stakeholder"}


def build(composed: Composed, facts: Sequence[fx.Fact], timeline: str, conflicts: Sequence[str] = (),
          notes: Sequence[str] = (), partial: bool = False, audience: str = "") -> dict:
    by_id = {f.id: f for f in facts}
    confidence = _confidence(composed, bool(conflicts), partial)
    needs_human = composed.ask_fallback or not composed.impact_stated or confidence == "low"
    return {
        "summary": " ".join(s for s, _ in composed.sentences) if not composed.bullets else bullets(facts),
        "summary_format": "bullets" if composed.bullets else "paragraph",
        "ask": composed.ask,
        "customer_impact": composed.impact,
        "items": fx.to_state(facts),
        "timeline": timeline,
        "sources": _sources(composed, by_id, facts),
        "conflicts": list(conflicts),
        "left_out": composed.dropped,
        "notes": list(notes),
        "audience": audience,
        "confidence": confidence,
        "needs_human": needs_human,
    }


def bullets(facts: Sequence[fx.Fact]) -> str:
    """The fallback when no paragraph could be backed: the checked facts, labelled, in thread order."""
    return "\n".join(f"- {KIND_LABELS.get(f.kind, f.kind)}: {f.text}" + (f" ({f.message})" if f.message else "")
                     for f in facts)


def _confidence(composed: Composed, has_conflict: bool, partial: bool) -> str:
    if has_conflict or partial or composed.bullets or composed.dropped > LOW_DROP_THRESHOLD:
        return "low"
    clean = composed.dropped == 0 and composed.replaced == 0
    return "high" if clean and not composed.ask_fallback and composed.impact_stated else "medium"


def _sources(composed: Composed, by_id: dict, facts: Sequence[fx.Fact]) -> list[dict]:
    out = []
    for sentence, ids in composed.sentences:
        out += [_source(sentence, by_id[i]) for i in ids if i in by_id]
    if not composed.ask_fallback:
        out += [_source("Ask", f) for f in fx.of_kind(facts, "ask")]
    if composed.impact_stated:
        out += [_source("Customer impact", f) for f in fx.of_kind(facts, "impact")]
    return out


def _source(sentence: str, fact: fx.Fact) -> dict:
    return {"sentence": sentence, "snippet": fact.snippet, "speaker": fact.speaker, "message": fact.message}
