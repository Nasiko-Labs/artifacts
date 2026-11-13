"""Pure: the plan this conversation is working on, recovered from the platform's history slice (this agent
stores nothing between messages).

The newest assistant reply that shows a plan is read back (plan_text.py). Code-only changes the user asked
for after it ("we are 2 people", "T1 is done", "4 units per week") are replayed from the later user turns,
since those replies did not repeat the whole plan. If the newest plan reply is cut off, the plan counts as
lost (`trimmed`): an older, complete plan is never used in its place, because it would be stale."""

from __future__ import annotations

import re
from dataclasses import dataclass

import plan_text
import queries
from history import ASSISTANT, USER, Turn
from models import Plan

_PLAN_LIKE = re.compile(r"^\*\*Plan: |^# Task plan: |```json\s*\{.*\"items\"", re.MULTILINE | re.DOTALL)
_REPLAYED = ("team", "rate", "done")
_FOOTER = re.compile(r"^_Draft only:.*$|^\(empty; add dated lines as work happens\)$", re.MULTILINE)


@dataclass(frozen=True)
class Recalled:
    plan: Plan | None = None
    trimmed: bool = False     # a plan reply is in the history but could not be read whole


def looks_like_plan(text: str) -> bool:
    return bool(_PLAN_LIKE.search(text or ""))


def from_history(turns: tuple[Turn, ...]) -> Recalled:
    index = next((i for i in range(len(turns) - 1, -1, -1)
                  if turns[i].role == ASSISTANT and looks_like_plan(turns[i].text)), None)
    if index is None:
        return Recalled()
    source = "\n".join(t.text for t in turns if t.role == USER)
    parts = None if turns[index].compressed else plan_text.read(turns[index].text)
    plan = plan_text.build(parts, source) if parts else None
    if plan is None:
        return Recalled(trimmed=True)
    return Recalled(_replay(plan, turns[index + 1:]))


def _replay(plan: Plan, later: tuple[Turn, ...]) -> Plan:
    for turn in later:
        command = queries.parse(turn.text) if turn.role == USER else None
        if command and command.kind in _REPLAYED:
            plan = queries.with_change(plan, command) or plan
    return plan


def pasted(text: str) -> tuple[Plan | None, str]:
    """A plan the user pasted back into this message (as asked when the history lost it), and the rest of
    the message (an instruction before the plan, or after its footer). (None, text) when there is none."""
    match = _PLAN_LIKE.search(text or "")
    if not match:
        return None, text
    parts = plan_text.read(text[match.start():])
    plan = plan_text.build(parts, text) if parts else None
    if plan is None:
        return None, text
    footers = list(_FOOTER.finditer(text, match.start()))
    after = text[footers[-1].end():].strip().strip("`").strip() if footers else ""
    return plan, "\n".join(p for p in (text[:match.start()].strip(), after) if p)
