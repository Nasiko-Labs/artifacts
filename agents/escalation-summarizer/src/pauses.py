"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and
how an answer is read. Reading fails closed: an unrecognised answer is Cancel."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import prompts
from inputs import MAX_THREAD_CHARS, MIN_THREAD_WORDS, current_message, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "escalation_summarizer"
NEED_THREAD, TOO_BIG = "need_thread", "too_big"
CANCEL = "Cancel"
MOST_RECENT = f"Summarize the most recent {MAX_THREAD_CHARS:,} characters"
NEED_THREAD_HEADER = "Paste the escalation thread"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|do it|proceed|most recent|the most recent|"
                  r"summari[sz]e the most recent)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    thread: str = ""          # too_big: the most recent part, already cut
    chars: int = 0
    attempts: int = 0
    notes: tuple[str, ...] = ()

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "thread": self.thread,
                               "chars": self.chars, "attempts": self.attempts, "notes": list(self.notes)}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_THREAD, TOO_BIG):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       thread=str(data.get("thread") or ""), chars=int(data.get("chars") or 0),
                       attempts=int(data.get("attempts") or 0), notes=tuple(str(n) for n in data.get("notes") or ()))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel | reask | give_up
    thread: str = ""
    instruction: str = ""
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_THREAD:
        header = NEED_THREAD_HEADER
        text = (f"Paste the escalation thread (at least about {MIN_THREAD_WORDS} words: ticket comments, a chat or "
                "email chain, internal notes) as your answer, or cancel.")
        if pending.attempts:
            text = "That's still too short to summarize. " + text
        options = [(CANCEL, "")]
    else:
        header = (f"This thread is {pending.chars:,} characters, over my {MAX_THREAD_CHARS:,} limit. "
                  "What should I do?")
        text = header
        options = [(MOST_RECENT, "The latest messages matter most in an escalation."), (CANCEL, "")]
    lead = "\n".join(pending.notes)
    return "\n\n".join(p for p in (lead, text) if p), question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_THREAD:
        return _need_thread(pending, answer, plain)
    if plain == MOST_RECENT.casefold() or _YES.match(plain):
        return Answer("run", pending.thread, pending.instruction)
    return Answer("cancel")


def _need_thread(pending: Pending, answer: str, plain: str) -> Answer:
    if word_count(answer) >= MIN_THREAD_WORDS:
        return Answer("run", prompts.strip_markers(answer), pending.instruction)
    if _CANCEL.match(plain) and word_count(answer) <= 3:
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=replace(pending, attempts=1, notes=()))
    return Answer("give_up")
