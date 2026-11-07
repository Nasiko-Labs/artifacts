"""Pure: keep only what the thread itself proves. The answer quote must appear verbatim (whitespace and
case relaxed) in ONE reply, and that reply must not be a non-answer ("let me check", "+1"). Who
answered and when come from that reply, never from the model. The question is the model's quote when it
is verbatim in the first message, else the first message's own text."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from models import Message, Thread

QUESTION_CHARS = 240
MIN_QUOTE_CHARS = 3
_SPACES = re.compile(r"\s+")
_NON_ANSWER = re.compile(
    r"^(?:\+1|same|same here|same question|following|following this|subscribing|bump|any update|any updates|"
    r"let me check|let me look|i'?ll check|i'?ll look|checking|looking into it|good question|no idea|not sure|"
    r"thanks|thank you|ty|thx|cheers|me too|\W+)\W*$", re.IGNORECASE)

Drop = Literal["not_same", "unanswered", "quote", "non_answer"]


@dataclass(frozen=True)
class Candidate:
    thread: Thread
    same: Literal["yes", "related"]
    answer: Message
    answer_quote: str
    question: str


def norm(text: str) -> str:
    return _SPACES.sub(" ", text or "").strip().casefold()


def is_non_answer(text: str) -> bool:
    return bool(_NON_ANSWER.match(norm(text)))


def find(quote: Any, messages: tuple[Message, ...]) -> Message | None:
    if not isinstance(quote, str) or len(norm(quote)) < MIN_QUOTE_CHARS:
        return None
    needle = norm(quote)
    return next((m for m in messages if needle in norm(m.text)), None)


def check(verdict: Mapping[str, Any], thread: Thread) -> tuple[Candidate | None, Drop | None]:
    same = str(verdict.get("same_question") or "").strip().casefold()
    if same not in ("yes", "related"):
        return None, "not_same"
    if verdict.get("answered") is not True:
        return None, "unanswered"
    answer = find(verdict.get("answer_quote"), thread.replies)
    if answer is None:
        return None, "quote"
    if is_non_answer(answer.text):
        return None, "non_answer"
    return Candidate(thread, same, answer, " ".join(str(verdict["answer_quote"]).split()),
                     _question(verdict.get("question_quote"), thread)), None


def _question(quote: Any, thread: Thread) -> str:
    root = thread.root
    if root is None:
        return "unknown"
    if find(quote, (root,)) is not None:
        return " ".join(str(quote).split())
    flat = " ".join(root.text.split())
    return flat if len(flat) <= QUESTION_CHARS else flat[:QUESTION_CHARS] + "…"
