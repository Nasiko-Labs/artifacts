"""The three questions this agent may pause on (one per pause, rendered by the chat's request card) and
how an answer is read. Reading fails closed: an answer that isn't recognised never widens what runs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import prompts
from inputs import MIN_SOURCE_WORDS, current_message, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "summarizer"
NEED_SOURCE, TOO_BIG, COMBINE = "need_source", "too_big", "combine"
CANCEL = "Cancel"
FIRST_PART = "Summarize the first ~20k words"
ON_ITS_OWN = "On its own"
TOGETHER = "Together with the earlier text"
NARROWER_MIN_WORDS = 3

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|do it|proceed|first|the first|summari[sz]e the first)\b")
_ALONE = re.compile(r"^(?:on its own|its own|own|alone|separately|separate|just this|only this|this one)\b")
_TOGETHER = re.compile(r"^(?:together|combined?|both|with the earlier|merge)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    source: str = ""          # too_big: the first part, already cut; combine: the new text
    words: int = 0
    attempts: int = 0
    can_combine: bool = False
    notes: tuple[str, ...] = ()

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "source": self.source,
                               "words": self.words, "attempts": self.attempts, "can_combine": self.can_combine,
                               "notes": list(self.notes)}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_SOURCE, TOO_BIG, COMBINE):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       source=str(data.get("source") or ""), words=int(data.get("words") or 0),
                       attempts=int(data.get("attempts") or 0), can_combine=bool(data.get("can_combine")),
                       notes=tuple(str(n) for n in data.get("notes") or ()))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | together | cancel | reask | give_up
    source: str = ""
    instruction: str = ""
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_SOURCE:
        header = "Paste the text you want summarized"
        text = "Paste the text you want summarized (at least about 60 words) as your answer, or cancel."
        if pending.attempts:
            text = "That's still too short to summarize. " + text
        options = [(CANCEL, "")]
    elif pending.kind == TOO_BIG:
        header = (f"This is about {pending.words:,} words, more than I can summarize in one go (~20k words). "
                  "What should I do?")
        text = header + " You can also type a narrower request (for example, a topic to focus on)."
        options = [(FIRST_PART, ""), (CANCEL, "")]
    else:
        header = "Summarize this on its own or together with the earlier text?"
        text = header
        options = [(ON_ITS_OWN, ""), *([(TOGETHER, "")] if pending.can_combine else []), (CANCEL, "")]
    lead = "\n".join(pending.notes)
    return "\n\n".join(p for p in (lead, text) if p), question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_SOURCE:
        return _need_source(pending, answer, plain)
    if _CANCEL.match(plain) or not plain:
        return Answer("cancel")
    if pending.kind == TOO_BIG:
        if plain == FIRST_PART.casefold() or _YES.match(plain):
            return Answer("run", pending.source, pending.instruction)
        if word_count(answer) >= NARROWER_MIN_WORDS:  # a narrower request over the first part
            return Answer("run", pending.source, prompts.strip_markers(answer)[:300])
        return Answer("cancel")
    if plain == ON_ITS_OWN.casefold() or _ALONE.match(plain):
        return Answer("run", pending.source, pending.instruction)
    if pending.can_combine and (plain == TOGETHER.casefold() or _TOGETHER.match(plain)):
        return Answer("together", pending.source, pending.instruction)
    return Answer("cancel")


def _need_source(pending: Pending, answer: str, plain: str) -> Answer:
    if word_count(answer) >= MIN_SOURCE_WORDS:
        return Answer("run", answer, pending.instruction)
    if _CANCEL.match(plain) and word_count(answer) <= 3:
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=replace(pending, attempts=1, notes=()))
    return Answer("give_up")
