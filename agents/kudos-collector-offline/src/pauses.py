"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: an answer that isn't recognised is a Cancel.

- Q1 over cap: more than 400 records -> `Use the 400 most recent` / `Cancel`.
- Q2 nobody identifiable in any record -> `Cancel`, or custom text = an alias map (`@pdev = Priya Dev`)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aliases as aliases_mod
from inputs import current_message
from nasiko_hitl import question_metadata
from options import MAX_RECORDS

METADATA_KEY = "kudos_collector"
OVER_CAP, NO_PEOPLE = "over_cap", "no_people"
USE_RECENT = f"Use the {MAX_RECORDS} most recent"
CANCEL = "Cancel"
NO_PEOPLE_HEADER = "I can't tell who anyone is. Add names or aliases?"
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|do it|proceed|use|most recent|the most recent)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    text: str            # the paste the question is about
    count: int = 0
    add: bool = False    # the paste was an "add these" to a stored result

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "text": self.text, "count": self.count, "add": self.add}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (OVER_CAP, NO_PEOPLE):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(data["kind"], str(data.get("text") or ""), int(data.get("count") or 0), bool(data.get("add")))


@dataclass(frozen=True)
class Answer:
    action: str                      # use_recent | aliases | cancel
    aliases: tuple[tuple[str, str], ...] = ()


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == OVER_CAP:
        header = f"That's {pending.count} records; I collect at most {MAX_RECORDS} per run. Use the most recent?"
        return header, question_metadata(header, [(USE_RECENT, ""), (CANCEL, "")], allow_custom_input=False)
    text = (NO_PEOPLE_HEADER + " None of the records has an author, an owner, an @handle or a name. Type an alias "
            "map, one per line (`@pdev = Priya Dev`), or cancel.")
    return text, question_metadata(NO_PEOPLE_HEADER, [(CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or _CANCEL.match(plain):
        return Answer("cancel")
    if pending.kind == OVER_CAP:
        return Answer("use_recent") if plain == USE_RECENT.casefold() or _YES.match(plain) else Answer("cancel")
    found = tuple(aliases_mod.parse(answer.replace(",", "\n").split("\n")).items())
    return Answer("aliases", found) if found else Answer("cancel")
