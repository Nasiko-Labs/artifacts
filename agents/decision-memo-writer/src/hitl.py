"""The three questions this agent may pause on (one per pause, rendered by the chat's request card) and
how an answer is read. Reading fails closed: an unrecognised short answer is Cancel, never a guess."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

import options as opts
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "decision_memo"
NO_OPTIONS, TOO_MANY, OVERSIZE = "no_options", "too_many", "oversize"
KINDS = (NO_OPTIONS, TOO_MANY, OVERSIZE)
MAX_BRIEF_CHARS = 8_000
CANCEL = "Cancel"
FIRST_FIVE = "The first 5"
USE_FIRST = "Use the first 8,000 characters"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_KEYS = re.compile(r"^(?:options?\s+)?[a-t](?:\s*(?:,|and|&|\s)\s*[a-t])*$")


@dataclass(frozen=True)
class Pending:
    kind: str
    text: str = ""
    count: int = 0
    chars: int = 0
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: asdict(self)}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in KINDS:
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], text=str(data.get("text") or ""), count=int(data.get("count") or 0),
                       chars=int(data.get("chars") or 0), wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str             # run | cancel
    text: str = ""          # the brief to run
    keys: tuple[str, ...] = ()
    note: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NO_OPTIONS:
        header = "What are the options?"
        text = "I couldn't find the options to compare. Reply with the options, one per line (or cancel)."
        return text, question_metadata(header, [(CANCEL, "Stop here; no memo is written.")])
    if pending.kind == TOO_MANY:
        header = f"That's {pending.count} options. Which should I compare?"
        text = (f"{header} A memo compares at most 5. Pick the first 5, or reply with the letters of the ones to "
                "keep (for example: A, C, D).")
        return text, question_metadata(header, [(FIRST_FIVE, "Compare options A to E."), (CANCEL, "")])
    header = "That's a lot of material. What should I use?"
    text = (f"That brief is {pending.chars:,} characters; I write from at most 8,000. Use the first 8,000 "
            "characters, or paste a shorter brief.")
    return text, question_metadata(header, [(USE_FIRST, ""), (CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or _CANCEL.match(plain):
        return Answer("cancel")
    if pending.kind == NO_OPTIONS:
        found = opts.from_section(answer)
        if len(found) < 2 and len(plain.split()) < 2:  # a lone short word is not an option list
            return Answer("cancel")
        return Answer("run", text=pending.text + "\n\nOptions:\n" + "\n".join(f"- {f}" for f in found))
    if pending.kind == TOO_MANY:
        if plain.rstrip(".") == FIRST_FIVE.casefold() or plain.startswith("first"):
            return Answer("run", text=pending.text, keys=tuple(opts.KEYS[:opts.MAX_OPTIONS]))
        if _KEYS.match(plain):
            keys = tuple(dict.fromkeys(k.upper() for k in re.findall(r"\b[a-t]\b", plain.removeprefix("options"))))
            if 1 <= len(keys) <= opts.MAX_OPTIONS:
                return Answer("run", text=pending.text, keys=keys)
        return Answer("cancel")
    if plain.rstrip(".") == USE_FIRST.casefold() or plain.startswith(("use", "yes", "ok", "first")):
        note = f"I used the first {MAX_BRIEF_CHARS:,} of {pending.chars:,} characters."
        return Answer("run", text=pending.text[:MAX_BRIEF_CHARS], note=note)
    return Answer("cancel")
