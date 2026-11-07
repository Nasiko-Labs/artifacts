"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: unrecognised short text is Cancel, never a guess."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import tasks as tasks_mod
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "prioritizer"
NEED_TASKS, TOO_MANY = "need_tasks", "too_many"
CANCEL = "Cancel"
FIRST_40 = f"The first {tasks_mod.MAX_TASKS}"
MIN_TASKS = 2

_FIRST = re.compile(r"^(?:rank |use )?(?:the )?first(?: \d+)?\W*$")


@dataclass(frozen=True)
class Pending:
    kind: str
    tasks: tuple[str, ...] = ()       # too_many: every parsed task, in order
    goals: tuple[str, ...] = ()
    instruction: str = ""
    preset: str = ""
    as_of: str = ""
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "tasks": list(self.tasks), "goals": list(self.goals),
                               "instruction": self.instruction, "preset": self.preset, "as_of": self.as_of,
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_TASKS, TOO_MANY):
            return None
        return Pending(kind=data["kind"], tasks=tuple(str(t) for t in data.get("tasks") or ()),
                       goals=tuple(str(g) for g in data.get("goals") or ()),
                       instruction=str(data.get("instruction") or ""), preset=str(data.get("preset") or ""),
                       as_of=str(data.get("as_of") or ""), wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                       # run | cancel | too_many
    tasks: tuple[str, ...] = ()
    goals: tuple[str, ...] = ()


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_TASKS:
        header = "Paste your tasks, one per line"
        text = "I need at least two tasks to rank. Paste them, one per line (add goals or a date if you like), or cancel."
        return text, question_metadata(header, [(CANCEL, "")])
    count = len(pending.tasks)
    header = f"That's {count} tasks. Which should I rank?"
    text = (f"{header} I rank up to {tasks_mod.MAX_TASKS} at a time. Pick the first {tasks_mod.MAX_TASKS}, or "
            "paste a shorter list.")
    return text, question_metadata(header, [(FIRST_40, ""), (CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    parsed = tasks_mod.parse(answer)
    goals = parsed.goals or pending.goals
    if len(parsed.tasks) > tasks_mod.MAX_TASKS:
        return Answer("too_many", parsed.tasks, goals)
    if len(parsed.tasks) >= MIN_TASKS:
        return Answer("run", parsed.tasks, goals)
    if pending.kind == TOO_MANY and (plain == FIRST_40.casefold() or _FIRST.match(plain)):
        return Answer("run", pending.tasks[:tasks_mod.MAX_TASKS], pending.goals)
    return Answer("cancel")
