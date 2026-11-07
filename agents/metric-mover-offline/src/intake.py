"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                          → canned intro (no call)
    a table in the message, a file or an envelope ─┬─ ambiguous before/after → pause: which column is before
                                                   ├─ unreadable             → fixed reply
                                                   └─ otherwise              → analyse (≤ 2 calls)
    a result stored ─┬─ re-render command          → render (no call)
                     ├─ "add event: …"             → store it, re-run the hypotheses (1 call)
                     ├─ "ignore <segment>"         → recompute in code, re-run the hypotheses (1 call)
                     └─ any other message          → one scoped question (1 call: answer or decline)
    nothing stored ─┬─ asks to explain a metric    → pause: paste the table (no call)
                    └─ anything else               → one scope-checked call (declined, or pause)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import events as ev
import followup
import hitl
import prompts
import smalltalk
from frame import Frame
from grid import find_grid
from inputs import Incoming
from table import detect

QUESTION_MAX_CHARS = 500
_METRIC_ASK = re.compile(r"\b(?:metric|kpi|conversion|signups?|revenue|churn|retention|dropped|fell|declined|"
                         r"spiked|why did|why is|why has|before/after|before and after|explain (?:the|a|this|my) "
                         r"(?:change|drop|move))\b", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                    # reply | pause | analyze | rerender | add_event | ignore | question | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    frame: Frame | None = None
    text: str = ""                # analyze: the text around the table; else the user's message
    view: str = "default"
    targets: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, stored: Frame | None) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    text = prompts.strip_markers(incoming.text)
    found = _from_message(text, incoming.files, None)
    if found is not None:
        return replace(found, notes=(*found.notes, *incoming.notes))
    message = text.strip()[:QUESTION_MAX_CHARS]
    if stored is not None:
        return _on_stored(message, stored, incoming.notes)
    if _METRIC_ASK.search(message):
        return Plan("pause", pending=hitl.Pending(hitl.NEED_SOURCE, question=message), notes=incoming.notes)
    return Plan("scope", text=message, notes=incoming.notes)


def plan_resume(answer: hitl.Answer) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_SOURCE_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    found = _from_message(prompts.strip_markers(answer.text), (), answer.forced)
    return found or Plan("reply", prompts.NO_SOURCE_GIVEN_UP)


def _from_message(text: str, files: tuple[tuple[str, str], ...], forced: tuple[str, str] | None) -> Plan | None:
    """A plan for the table in a file part, an envelope or the pasted text; None when there is no table."""
    for _, file_text in files:
        grid = find_grid(prompts.strip_markers(file_text))
        if grid is not None:
            return _detected(grid, text, text, forced)
    grid = find_grid(text)
    if grid is None:
        return None
    return _detected(grid, grid.question, text, forced)


def _detected(grid, around: str, whole: str, forced: tuple[str, str] | None) -> Plan:
    found = detect(grid, forced)
    if found.ambiguous:
        pending = hitl.Pending(hitl.WHICH_BEFORE, source=whole, options=found.ambiguous)
        return Plan("pause", pending=pending)
    if found.frame is None:
        return Plan("reply", f"{found.problem} {prompts.TABLE_HINT}")
    return Plan("analyze", frame=found.frame, text=around.strip())


def _on_stored(message: str, stored: Frame, notes: tuple[str, ...]) -> Plan:
    view = followup.parse_command(message, stored.dims)
    if view is not None:
        return Plan("rerender", view=view, notes=notes)
    if ev.is_add_event(message):
        return Plan("add_event", text=message, notes=notes)
    targets = followup.ignore_targets(message, stored.segment_names())
    if targets is not None:
        return Plan("ignore", text=message, targets=tuple(targets), notes=notes)
    return Plan("question", text=message, notes=notes)
