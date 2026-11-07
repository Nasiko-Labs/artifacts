"""One untangle: extract calls → code checks → reversal pass → verify and apply → caps, TL;DR,
confidence and needs_human, all computed in code."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

import checks
import extract
import grounding as g
import llm
import reversals
from model import NOT_STATED, Action, Untangled
from thread import UNKNOWN, Thread

DECISIONS_PER_WINDOW = 10
DECISION_WINDOW_CHARS = 24_000
MAX_DECISIONS = 20
MAX_QUESTIONS = 15
MAX_ACTIONS = 25
TLDR_MAX_SENTENCES = 3
TLDR_SENTENCE_MAX_WORDS = 45
HIGH_DROP_SHARE = 0.10
LOW_DROP_SHARE = 0.30
LOW_COVERAGE = 0.70

_SENTENCES = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Outcome:
    status: str                   # ok | off_topic | failed
    result: Untangled | None = None


async def untangle(deps: llm.TurnDeps, thread: Thread, instruction: str, context: Sequence[str] = (),
                   notes: Sequence[str] = ()) -> Outcome:
    mapped = await extract.run(deps, thread, instruction, context)
    if not mapped.parts:
        return Outcome("off_topic" if mapped.off_topic else "failed")
    parts = [part for _, part in mapped.parts]
    decisions = checks.by_message(checks.dedup((d for p in parts for d in p.decisions), lambda d: d.text), thread)
    questions = checks.by_message(checks.dedup((q for p in parts for q in p.questions), lambda q: q.text), thread)
    actions = checks.by_message(checks.dedup((a for p in parts for a in p.actions), lambda a: a.task,
                                             lambda a: a.owner), thread)
    raw = [r for p in parts for r in p.reversals]
    if reversals.wanted(decisions, len(mapped.chunks)) and deps.remaining() > 1:
        raw += await reversals.find(deps, decisions, thread, context)
    verified, reversal_drops = reversals.verify(raw, decisions, thread)
    decisions, actions = reversals.apply(decisions, actions, verified)

    chars = sum(len(m.text) for m in thread.content)
    decision_cap = min(MAX_DECISIONS, DECISIONS_PER_WINDOW * max(1, math.ceil(chars / DECISION_WINDOW_CHARS)))
    capped = sum(max(0, len(items) - cap) for items, cap in
                 ((decisions, decision_cap), (questions, MAX_QUESTIONS), (actions, MAX_ACTIONS)))
    kept = sum(p.kept for p in parts) + len(verified)
    dropped = sum(p.dropped for p in parts) + reversal_drops
    decisions, questions, actions = decisions[:decision_cap], questions[:MAX_QUESTIONS], actions[:MAX_ACTIONS]
    tldr = model_tldr(parts[0].tldr, thread) if len(mapped.chunks) == 1 else ""
    confidence = rate(thread, mapped, dropped, kept)
    result = Untangled(
        tldr=tldr or code_tldr(len(decisions), len(questions), len(actions), thread),
        decisions=decisions, questions=questions, actions=actions, reversals=verified, dropped=dropped,
        capped=capped, messages=len(thread.content), people=len(thread.speakers), chunks=len(mapped.chunks),
        confidence=confidence, needs_human=needs_human(confidence, actions, verified),
        notes=(*notes, *mapped.notes()))
    return Outcome("ok", result)


def model_tldr(text: str, thread: Thread) -> str:
    """The model's TL;DR, keeping only sentences whose numbers and names are all in the thread."""
    full = "\n".join(m.text for m in thread.messages)
    norm = g.normalise(full)
    cleaned = checks.clean_text(text, full)
    kept = [s for s in _SENTENCES.split(cleaned) if s.strip() and g.is_grounded(s, norm)]
    return " ".join(checks.cut_words(s, TLDR_SENTENCE_MAX_WORDS) for s in kept[:TLDR_MAX_SENTENCES])


def code_tldr(decisions: int, questions: int, actions: int, thread: Thread) -> str:
    counts = f"{_n(decisions, 'decision')}, {_n(questions, 'open question')}, {_n(actions, 'action item')}"
    speakers = [m.speaker for m in thread.content if m.speaker != UNKNOWN]
    others = [s for s in speakers if s != speakers[0]] if speakers else []
    if others:  # the first speaker and the last one who is someone else
        return f"{counts} between {speakers[0]} and {others[-1]}."
    return f"{counts} in the thread."


def rate(thread: Thread, mapped: extract.Mapped, dropped: int, kept: int) -> str:
    share = dropped / (dropped + kept) if dropped + kept else 0.0
    if share > LOW_DROP_SHARE or mapped.failed >= 2 or mapped.coverage < LOW_COVERAGE:
        return "low"
    headers = thread.fmt != "paragraphs" and len(thread.content) >= 3 and len(thread.speakers) >= 2
    if not headers or share > HIGH_DROP_SHARE or mapped.failed == 1 or mapped.coverage < 1.0:
        return "medium"
    return "high"


def needs_human(confidence: str, actions: Sequence[Action], verified: Sequence) -> bool:
    open_actions = [a for a in actions if a.status != "superseded"]
    unowned = any(a.owner == NOT_STATED for a in open_actions)
    touched = bool(verified) and any(a.status == "superseded" for a in actions)
    return confidence == "low" or unowned or touched


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")
