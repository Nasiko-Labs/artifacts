"""The one question this agent may pause on (goal too vague), rendered by the chat's request card, and how
an answer is read. One pause at most per request: after it the agent plans on what it has."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "task_breakdown"
VAGUE = "vague_goal"
HEADER = "What should the end result be?"
OPTIONS = ("A feature or product", "A project at work", "An event or launch", "A document or report")
CANCEL = "Cancel"
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    goal: str = ""
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "goal": self.goal, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != VAGUE:
            return None
        return Pending(VAGUE, str(data.get("goal") or ""), bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str          # plan | cancel
    text: str = ""       # what to plan from
    reply: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    text = (f"{HEADER} Pick the closest kind, or better, type the goal in a sentence (for example \"launch our "
            "beta signup page by the end of the quarter\").")
    return text, question_metadata(HEADER, [(label, "") for label in (*OPTIONS, CANCEL)])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or _CANCEL.match(plain):
        return Answer("cancel", reply=prompts.CANCELLED)
    label = next((o for o in OPTIONS if o.casefold() == plain), None)
    if label:
        goal = pending.goal.strip() or "Plan the work"
        return Answer("plan", f"{goal} ({label.lower()})")
    return Answer("plan", answer if not pending.goal else f"{answer}\n\n(Earlier: {pending.goal})")
