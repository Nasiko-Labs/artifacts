"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: an answer that isn't recognised is a Cancel."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import prompts
from inputs import (
    MAX_DRAFT_CHARS,
    MIN_DRAFT_WORDS,
    current_message,
    first_part,
    word_count,
)
from nasiko_hitl import question_metadata

METADATA_KEY = "humanizer"
NEED_DRAFT, TOO_BIG = "need_draft", "too_big"
CANCEL = "Cancel"
FIRST_PART = "The first ~3k words"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|do it|proceed|first|the first)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    request: str = ""          # the request words (options are re-parsed from them on resume)
    draft: str = ""            # too_big: the first part, already cut
    words: int = 0
    attempts: int = 0

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "request": self.request, "draft": self.draft,
                               "words": self.words, "attempts": self.attempts}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_DRAFT, TOO_BIG):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], request=str(data.get("request") or ""), draft=str(data.get("draft") or ""),
                       words=int(data.get("words") or 0), attempts=int(data.get("attempts") or 0))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel | reask | give_up
    draft: str = ""
    request: str = ""
    notes: tuple[str, ...] = ()
    pending: Pending | None = None


def need_draft(request: str) -> Pending:
    return Pending(NEED_DRAFT, request=prompts.strip_markers(request)[:300])


def too_big(request: str, draft: str) -> Pending:
    return Pending(TOO_BIG, request=prompts.strip_markers(request)[:300], draft=first_part(draft),
                   words=word_count(draft))


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_DRAFT:
        header = "Paste the draft to humanize"
        text = f"Paste the draft you want humanized (at least {MIN_DRAFT_WORDS} words) as your answer, or cancel."
        if pending.attempts:
            text = "That's too short to humanize. " + text
        return text, question_metadata(header, [(CANCEL, "")])
    header = f"That's about {pending.words:,} words. What should I humanize?"
    text = header + " Pick the first ~3k words, or paste a shorter section as your answer."
    return text, question_metadata(header, [(FIRST_PART, ""), (CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = prompts.strip_markers(current_message(raw_answer or "")).strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_DRAFT:
        return _need_draft(pending, answer, plain)
    if _CANCEL.match(plain) or not plain:
        return Answer("cancel")
    if plain == FIRST_PART.casefold() or (_YES.match(plain) and word_count(answer) <= 4):
        return Answer("run", pending.draft, pending.request, (prompts.FIRST_PART_NOTE,))
    if word_count(answer) >= MIN_DRAFT_WORDS:  # a shorter section, pasted as the answer
        if len(answer) > MAX_DRAFT_CHARS:
            return Answer("run", first_part(answer), pending.request, (prompts.FIRST_PART_NOTE,))
        return Answer("run", answer, pending.request)
    return Answer("cancel")


def _need_draft(pending: Pending, answer: str, plain: str) -> Answer:
    if word_count(answer) >= MIN_DRAFT_WORDS:
        if len(answer) > MAX_DRAFT_CHARS:
            return Answer("run", first_part(answer), pending.request, (prompts.FIRST_PART_NOTE,))
        return Answer("run", answer, pending.request)
    if _CANCEL.match(plain) and word_count(answer) <= 3:
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=replace(pending, attempts=1))
    return Answer("give_up")
