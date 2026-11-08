"""The three questions this agent may pause on (one per pause, rendered by the chat's request card) and how
an answer is read. Reading fails closed: an answer that isn't recognised never widens what runs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import languages
import prompts
import request
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "translator"
NEED_LANGUAGE, NEED_TEXT, TOO_BIG = "need_language", "need_text", "too_big"
CANCEL = "Cancel"
FIRST_PART = "The first ~10k words"
LANGUAGE_OPTIONS = ("Spanish", "French", "German", "Portuguese", "Japanese", "Chinese (Simplified)", "Hindi")
MAX_SOURCE_CHARS = 60_000

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|do it|proceed|first|the first)\b")
UNSUPPORTED = ("I can't translate into \"{name}\" yet. I support: {names}. Send the text again with one of "
               "them.")


@dataclass(frozen=True)
class Pending:
    kind: str
    job: request.Job

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "job": self.job.to_dict()}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_LANGUAGE, NEED_TEXT, TOO_BIG):
            return None
        job = request.Job.from_dict(dict(data.get("job") or {}))
        return Pending(data["kind"], job) if job is not None else None


@dataclass(frozen=True)
class Answer:
    action: str                     # run | cancel | reply
    job: request.Job | None = None
    reply: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_LANGUAGE:
        header = "Which language should I translate into?"
        lead = (f"I don't know the language \"{pending.job.unknown}\". " if pending.job.unknown else "")
        text = lead + header + " Pick one or type any language."
        options = [(name, "") for name in LANGUAGE_OPTIONS]
    elif pending.kind == NEED_TEXT:
        header = "Paste the text to translate"
        text = header + " as your answer, or cancel."
        options = [(CANCEL, "")]
    else:
        header = "That's a lot of text. What should I translate?"
        text = header + f" I can take about {MAX_SOURCE_CHARS:,} characters (~10k words) in one go."
        options = [(FIRST_PART, ""), (CANCEL, "")]
    return text, question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = prompts.strip_markers(current_message(raw_answer or "")).strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_TEXT:
        return _need_text(pending, answer, plain)
    if not plain or (_CANCEL.match(plain) and len(plain.split()) <= 3):
        return Answer("cancel")
    if pending.kind == NEED_LANGUAGE:
        found = languages.from_answer(answer)
        if found is None:
            return Answer("reply", reply=UNSUPPORTED.format(name=answer[:40], names=", ".join(languages.supported_names())))
        return Answer("run", replace(pending.job, target=found.language.code, target_note=found.note, unknown=""))
    if plain == FIRST_PART.casefold() or _YES.match(plain):
        return Answer("run", pending.job)
    return Answer("cancel")


def _need_text(pending: Pending, answer: str, plain: str) -> Answer:
    if not plain or (_CANCEL.match(plain) and len(plain.split()) <= 3):
        return Answer("cancel")
    envelope = request.envelope_from(answer)
    return Answer("run", replace(pending.job, source="" if envelope else answer, envelope=envelope))


def cut(source: str, limit: int = MAX_SOURCE_CHARS) -> str:
    """The first `limit` chars, cut back to the last paragraph or line break when there is one."""
    if len(source) <= limit:
        return source
    head = source[:limit]
    for boundary in ("\n\n", "\n", ". "):
        at = head.rfind(boundary)
        if at >= limit // 2:
            return head[:at].rstrip()
    return head
