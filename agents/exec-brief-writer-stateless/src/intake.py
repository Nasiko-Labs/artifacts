"""Pure: decide what a turn does, in code, before any model call.

Returns a Step whose `route` is "compose" (with `work` for the compose step) or "" (the turn ends here: a
fixed reply or a code-only edit). Nothing ever pauses: where the original agent asked a question, this one
takes the least-friction default and says so (PLAN.md, "Former pauses"). What earlier turns produced comes
from the platform's history slice (recall.py), never from storage.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import commands
import prompts
import recall
import render
import sections as sec
from brief import Brief
from history import ASSISTANT
from inputs import Incoming, split_instruction, words

REVISION_JOIN = "\nRevision request: "


@dataclass(frozen=True)
class Work:
    sections: tuple[sec.Section, ...]
    instruction: str = ""
    prior: Brief | None = None        # the brief being revised (read back from the history)
    scope_only: bool = False
    followup: str = ""                # a free-text message on an earlier brief: scope-checked before revising
    previous_reply: str = ""          # the last assistant reply, the context of that check
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Step:
    route: str = ""                   # compose | "" (done)
    reply: str = ""
    work: Work | None = field(default=None)


def decide(incoming: Incoming) -> Step:
    query, files = incoming.text, incoming.files
    remembered = recall.from_history(incoming.history)
    command = commands.parse(query)
    if command.kind and not files:
        return _command(command, remembered)
    instruction, source = split_instruction(query)
    pasted = (*sec.split(source), *files) if (source or files) else ()
    if pasted:
        return _paste(remembered, pasted, instruction)
    if remembered.sections and remembered.shown is not None:
        joined = REVISION_JOIN.join(p for p in (remembered.instruction.strip(), query.strip()) if p)
        return Step("compose", work=Work(remembered.sections, joined, prior=remembered.shown.brief,
                                         followup=query.strip(), previous_reply=_last_reply(incoming)))
    if remembered.lost or remembered.shown is not None:
        return Step(reply=prompts.MATERIAL_NOT_IN_HISTORY)
    if words(query) and commands.asks_for_brief(query):
        return Step(reply=prompts.NEED_PASTE)
    return Step("compose", work=Work((), query, scope_only=True))


def _last_reply(incoming: Incoming) -> str:
    return next((t.text for t in reversed(incoming.history) if t.role == ASSISTANT), "")


def _paste(remembered: recall.Recalled, pasted: Sequence[sec.Section], instruction: str) -> Step:
    notes: list[str] = []
    stored = remembered.sections
    adding = commands.wants_add(instruction) and not commands.wants_new(instruction)
    earlier = remembered.lost or remembered.shown is not None
    if adding and not stored and earlier:
        notes.append(prompts.ADD_WITHOUT_EARLIER)
    elif earlier and not adding and not commands.wants_new(instruction):
        notes.append(prompts.NEW_BY_DEFAULT)
    final, joined, cut = recall.apply_paste(stored, remembered.instruction, pasted, instruction)
    if cut:
        total = len(stored) + len(pasted) if adding and stored else len(pasted)
        notes.append(prompts.CUT_TO_FIT.format(kept=len(final), total=total))
    return Step("compose", work=Work(final, joined, notes=tuple(notes)))


def _command(command: commands.Command, remembered: recall.Recalled) -> Step:
    if command.kind == "start_over":
        return Step(reply=prompts.CLEARED)
    shown = remembered.shown
    if shown is None:
        return Step(reply=prompts.MATERIAL_NOT_IN_HISTORY if remembered.lost else prompts.NO_BRIEF_YET)
    if command.kind == "show_sources":
        if not remembered.sections:
            return Step(reply=prompts.MATERIAL_NOT_IN_HISTORY)
        quoted = recall.with_quotes(shown.brief, remembered.sections)
        return Step(reply=render.render(quoted, remembered.sections, sources=True))
    edited, problem = commands.apply(shown.brief, command)
    if problem:
        return Step(reply=problem)
    return Step(reply=render.render(edited, shown.key))
