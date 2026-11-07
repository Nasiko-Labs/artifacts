"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                          -> canned intro (no call)
    a one-line command, a ranking stored           -> follow-up (re-rank / view: no call; add / rate again: calls)
    over 12,000 chars                              -> fixed reply
    over 40 tasks                                  -> pause: the first 40 or a shorter list
    2-40 tasks                                     -> rate + rank
    fewer than 2 tasks ─┬─ asks for a ranking      -> pause: paste your tasks (no call)
                        └─ anything else           -> one scope call (decline / pause / follow-up help)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import dates
import followup
import hitl
import prompts
import smalltalk
import tasks as tasks_mod
from inputs import Incoming

_RANK_REQUEST = re.compile(r"\b(?:prioriti[sz]\w*|rank\w*|triage|order my|sort my|my (?:tasks|to-?dos?|list|week|day)|"
                           r"what should i (?:do|work on|drop|delegate|focus on|tackle)|to-?do list)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                         # reply | pause | run | followup | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    parsed: tasks_mod.Parsed | None = None
    preset: str = ""
    as_of: str = ""
    command: followup.Command | None = None
    notes: tuple[str, ...] = ()


def preset_in(text: str) -> str:
    command = followup.parse(text)
    return command.text if command is not None and command.kind == "preset" else ""


def plan_turn(incoming: Incoming, has_ranking: bool) -> Route:
    text = incoming.text
    canned = smalltalk.reply_to(text)
    if canned is not None:
        return Route("reply", canned)
    single_line = "\n" not in text.strip() and ";" not in text
    if has_ranking and single_line:
        command = followup.parse(text)
        if command is not None:
            return Route("followup", command=command)
    if len(text) > tasks_mod.MAX_CHARS:
        return Route("reply", prompts.TOO_LONG)
    parsed = tasks_mod.parse(text)
    as_of = dates.parse_as_of(text)
    preset = preset_in(parsed.instruction)
    common = {"preset": preset, "as_of": as_of.isoformat() if as_of else ""}
    if len(parsed.tasks) > tasks_mod.MAX_TASKS:
        return Route("pause", pending=_pending(hitl.TOO_MANY, parsed, incoming, **common))
    if len(parsed.tasks) >= hitl.MIN_TASKS:
        return Route("run", parsed=parsed, notes=_notes(parsed), **common)
    if _RANK_REQUEST.search(text) and not has_ranking:
        return Route("pause", pending=_pending(hitl.NEED_TASKS, parsed, incoming, **common))
    return Route("scope", pending=_pending(hitl.NEED_TASKS, parsed, incoming, **common))


def plan_resume(answer: hitl.Answer, pending: hitl.Pending) -> Route:
    if answer.action == "cancel":
        return Route("reply", prompts.CANCELLED)
    parsed = tasks_mod.Parsed(answer.tasks, answer.goals, instruction=pending.instruction)
    if answer.action == "too_many":
        again = hitl.Pending(hitl.TOO_MANY, answer.tasks, answer.goals, pending.instruction, pending.preset,
                             pending.as_of, pending.wants_json)
        return Route("pause", pending=again)
    return Route("run", parsed=parsed, preset=pending.preset, as_of=pending.as_of)


def _pending(kind: str, parsed: tasks_mod.Parsed, incoming: Incoming, preset: str, as_of: str) -> hitl.Pending:
    return hitl.Pending(kind, parsed.tasks if kind == hitl.TOO_MANY else (), parsed.goals, parsed.instruction,
                        preset, as_of, incoming.wants_json)


def _notes(parsed: tasks_mod.Parsed) -> tuple[str, ...]:
    notes = []
    if parsed.done:
        notes.append(f"{parsed.done} done item{'s' if parsed.done != 1 else ''} skipped.")
    if parsed.merged:
        notes.append(f"{parsed.merged} duplicate{'s' if parsed.merged != 1 else ''} merged.")
    return tuple(notes)
