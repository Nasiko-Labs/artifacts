"""Pure: match strength, acknowledgement, age and order, all in code.

strong  = judged "yes" AND answered AND Jaccard(question terms, first-message terms) >= 0.35
partial = judged "related", or "yes" below that overlap
acknowledged = a positive reaction on the answer or the question, or a thanks-like reply from the asker
stale   = the answer is older than 180 days (labelled "may be out of date", never hidden)
Order: strong before partial; within a strength non-stale before stale, acknowledged first, newer first.
Shown: up to 3, and at most 1 partial when a strong one exists."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from models import Confidence, Message, PriorAnswer, utc
from shortlist import has_positive_reaction, lead_text
from terms import jaccard, terms
from verify import Candidate

STRONG_JACCARD = 0.35
STALE_DAYS = 180
MAX_SHOWN = 3
_THANKS = re.compile(r"\b(?:thanks|thank you|thx|ty|cheers|that worked|worked|perfect|got it|that did it|"
                     r"appreciate it|legend|lifesaver|solved|fixed it)\b", re.IGNORECASE)


def strength(same: str, overlap: float) -> str:
    return "strong" if same == "yes" and overlap >= STRONG_JACCARD else "partial"


def acknowledged(candidate: Candidate) -> bool:
    thread, answer = candidate.thread, candidate.answer
    if has_positive_reaction((answer,) + ((thread.root,) if thread.root else ())):
        return True
    root = thread.root
    if root is None:
        return False
    later = [m for m in thread.replies if _after(m, answer) and m.ts != answer.ts]
    return any(_same_person(m, root) and _THANKS.search(m.text) for m in later)


def _after(message: Message, other: Message) -> bool:
    if message.time is not None and other.time is not None:
        return message.time >= other.time
    return message.ts >= other.ts


def _same_person(a: Message, b: Message) -> bool:
    if a.user_id and b.user_id:
        return a.user_id == b.user_id
    return a.author.casefold() == b.author.casefold()


def build(candidate: Candidate, question_terms: frozenset[str], now: datetime | None) -> PriorAnswer:
    answer = candidate.answer
    overlap = jaccard(question_terms, terms(lead_text(candidate.thread)))
    when = utc(answer.ts) if not answer.time_label else None
    age = (now.date() - when.date()).days if (now is not None and when is not None) else None
    return PriorAnswer(
        question=candidate.question, answer=candidate.answer_quote, answered_by=answer.author,
        answered_at=when.date().isoformat() if when else (answer.time_label or "unknown"),
        channel=candidate.thread.channel_label, link=answer.permalink or _root_link(candidate) or None,
        match=strength(candidate.same, overlap), acknowledged=acknowledged(candidate), age_days=age,
        stale=age is not None and age > STALE_DAYS, thread_id=candidate.thread.key)


def _root_link(candidate: Candidate) -> str:
    root = candidate.thread.root
    return root.permalink if root else ""


def order(answers: Sequence[PriorAnswer]) -> tuple[PriorAnswer, ...]:
    return tuple(sorted(answers, key=lambda a: (a.match != "strong", a.stale, not a.acknowledged,
                                                a.age_days if a.age_days is not None else 10**6, a.thread_id)))


def select(ordered: Sequence[PriorAnswer]) -> tuple[PriorAnswer, ...]:
    """The answers to show: up to 3, at most 1 partial when a strong one exists."""
    pool = list(ordered)
    has_strong = any(a.match == "strong" for a in pool)
    shown: list[PriorAnswer] = []
    partials = 0
    for answer in pool:
        if answer.match == "partial" and has_strong and partials >= 1:
            continue
        partials += answer.match == "partial"
        shown.append(answer)
        if len(shown) == MAX_SHOWN:
            break
    return tuple(shown)


def confidence(shown: Sequence[PriorAnswer], *, narrower: bool, dropped: int, pasted: bool) -> Confidence:
    if pasted:
        return "low"
    if not shown:
        return "medium"
    best = shown[0]
    if best.match == "partial":
        return "low"
    base: Confidence = "high" if best.acknowledged and not best.stale else "medium"
    return "medium" if base == "high" and (narrower or dropped) else base


def needs_human(shown: Sequence[PriorAnswer]) -> bool:
    return not shown or shown[0].match == "partial" or shown[0].stale
