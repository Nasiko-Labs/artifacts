"""Pure: the code checks on the judge's answer. A decision survives only when its status is "decided", it
names a message read this turn, and its quote occurs verbatim in that message and in this turn's evidence.
Who decided, when, where and the link come from the message, never from the model. Questions are dropped
here too, whatever the model said. Reversals are linked in code."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from models import Decision, Message, text_of
from textcheck import appears_in

MAX_DECISION_CHARS = 140
_QUESTION = re.compile(r"^\s*(?:should|shall|could|can|do|does|did|would|will|are|is|what if|how about|why not)\b",
                       re.IGNORECASE)


_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


@dataclass(frozen=True)
class Candidate:
    decision: Decision
    supersedes_quote: str = ""
    order: int = 0


@dataclass(frozen=True)
class Verified:
    candidates: tuple[Candidate, ...] = ()
    dropped: int = 0      # named a message or quote that was not read this turn
    not_decided: int = 0  # questions and proposals left out


def is_question(text: str) -> bool:
    body = (text or "").strip()
    return body.endswith("?") or bool(_QUESTION.match(body))


def sentence_of(quote: str, text: str) -> str:
    """The sentence of `text` that holds `quote` (case-insensitive), so "use Redis" inside "should we use
    Redis?" reads as part of a question."""
    body = " ".join((text or "").split())
    at = body.casefold().find(" ".join((quote or "").split()).casefold())
    if at < 0:
        return quote
    starts = [m.end() for m in _SENTENCE_END.finditer(body[:at])]
    end = _SENTENCE_END.search(body, at + len(quote))
    return body[starts[-1] if starts else 0:end.end() if end else len(body)].strip()


def verify(raw: Sequence[Mapping[str, Any]], messages: Sequence[Message], evidence: str,
           start: int = 0) -> Verified:
    by_id = {m.id: m for m in messages}
    kept: tuple[Candidate, ...] = ()
    dropped = not_decided = 0
    for n, item in enumerate(raw):
        status = text_of(item.get("status")).casefold()
        quote = text_of(item.get("quote"))
        message = by_id.get(text_of(item.get("message")))
        if status != "decided":
            not_decided += 1
            continue
        if message is None or not appears_in(quote, message.text) or not appears_in(quote, evidence):
            dropped += 1
            continue
        if is_question(quote) or is_question(sentence_of(quote, message.text)):
            not_decided += 1
            continue
        kept = (*kept, Candidate(_decision(item, message, quote, by_id), text_of(item.get("supersedes_quote")),
                                 start + n))
    return Verified(kept, dropped, not_decided)


def _decision(item: Mapping[str, Any], message: Message, quote: str, by_id: Mapping[str, Message]) -> Decision:
    kind = "implied" if text_of(item.get("kind")).casefold() == "implied" else "explicit"
    proposer = by_id.get(text_of(item.get("proposer_message")))
    named = proposer.who if proposer is not None and proposer.id != message.id and proposer.who != "unknown" else None
    return Decision(id="", decision=short(text_of(item.get("decision")) or quote), decided_by=message.who,
                    decided_at=message.day, channel=message.where, quote=quote, kind=kind,
                    link=message.link or None, proposer=named if kind == "implied" else None)


def short(text: str) -> str:
    body = " ".join((text or "").split())
    return body if len(body) <= MAX_DECISION_CHARS else body[:MAX_DECISION_CHARS - 1].rstrip() + "…"


def finalize(candidates: Sequence[Candidate]) -> tuple[Decision, ...]:
    """Oldest first, one entry per quote, numbered 1..n, reversals linked to the earlier decision."""
    ordered = sorted(candidates, key=lambda c: (c.decision.decided_at, c.order))
    unique: tuple[Candidate, ...] = ()
    for c in ordered:
        if not any(_same(c.decision, u.decision) for u in unique):
            unique = (*unique, c)
    numbered = tuple(replace(c, decision=replace(c.decision, id=str(n))) for n, c in enumerate(unique, start=1))
    return tuple(_linked(c, numbered[:i]) for i, c in enumerate(numbered))


def _same(a: Decision, b: Decision) -> bool:
    return a.decided_at == b.decided_at and a.channel == b.channel and appears_in(a.quote, b.quote) \
        and appears_in(b.quote, a.quote)


def _linked(candidate: Candidate, earlier: Sequence[Candidate]) -> Decision:
    wanted = candidate.supersedes_quote
    if not wanted:
        return candidate.decision
    match = next((e.decision for e in reversed(earlier)
                  if appears_in(wanted, e.decision.quote) or appears_in(e.decision.quote, wanted)), None)
    if match is None:
        return candidate.decision
    return replace(candidate.decision, supersedes=match.id, supersedes_date=match.decided_at)
