"""Pure: decide what one turn does, before any LLM call. Nothing ever pauses: every route ends the task.

    greeting / small talk                              -> canned intro (no call)
    a one-line command, a ranking or list in history   -> follow-up (re-rank / view: no call when the ranking
                                                          is in history; a list found there is rated first)
    over 12,000 chars                                  -> rank what fits in the first 12,000, say what was left out
    over 40 tasks                                      -> rank the first 40, list what was left out
    2-40 tasks                                         -> rate + rank
    fewer than 2 tasks ─┬─ asks for a ranking          -> ask to paste the tasks, or to paste them again when
                        │                                 the earlier list is no longer in history (no call)
                        └─ anything else               -> one scope call (decline / ask / follow-up help)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import dates
import followup
import prompts
import smalltalk
import tasks as tasks_mod
from inputs import Incoming

MIN_TASKS = 2
LEFT_OUT_SHOWN = 10

_RANK_REQUEST = re.compile(r"\b(?:prioriti[sz]\w*|rank\w*|triage|order my|sort my|my (?:tasks|to-?dos?|list|week|day)|"
                           r"what should i (?:do|work on|drop|delegate|focus on|tackle)|to-?do list)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                         # reply | run | followup | scope
    reply: str = ""
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
    text, cut_note = _within_cap(text)
    parsed = tasks_mod.parse(text)
    as_of = dates.parse_as_of(text)
    preset = preset_in(parsed.instruction)
    common = {"preset": preset, "as_of": as_of.isoformat() if as_of else ""}
    if len(parsed.tasks) >= MIN_TASKS:
        kept, left_out = parsed.tasks[:tasks_mod.MAX_TASKS], parsed.tasks[tasks_mod.MAX_TASKS:]
        notes = (*cut_note, *_left_out_note(len(parsed.tasks), left_out), *_notes(parsed))
        return Route("run", parsed=_with_tasks(parsed, kept), notes=notes, **common)
    if _RANK_REQUEST.search(text) and not has_ranking:
        return Route("reply", ask_for_list(incoming))
    return Route("scope", **common)


def ask_for_list(incoming: Incoming) -> str:
    """No list to work on: with earlier turns it was trimmed from what the platform sent, so say so."""
    return prompts.NOT_IN_HISTORY if incoming.history else prompts.NEED_TASKS


def _within_cap(text: str) -> tuple[str, tuple[str, ...]]:
    """The text up to the character cap (cut at a line break when there is one) and a note on what was cut."""
    if len(text) <= tasks_mod.MAX_CHARS:
        return text, ()
    head = text[:tasks_mod.MAX_CHARS]
    cut = head.rfind("\n")
    kept = head[:cut] if cut > 0 else head
    dropped = len([line for line in text[len(kept):].splitlines() if line.strip()])
    lines = f"{dropped} lines after that were" if dropped != 1 else "1 line after that was"
    note = (f"_Your message was over {tasks_mod.MAX_CHARS:,} characters, so I only read the first {len(kept):,}; "
            f"{lines} left out. Paste them separately to rank them._")
    return kept, (note,)


def _left_out_note(total: int, left_out: tuple[str, ...]) -> tuple[str, ...]:
    if not left_out:
        return ()
    shown = "; ".join(left_out[:LEFT_OUT_SHOWN])
    more = f"; and {len(left_out) - LEFT_OUT_SHOWN} more" if len(left_out) > LEFT_OUT_SHOWN else ""
    note = (f"_You sent {total} tasks; I rank up to {tasks_mod.MAX_TASKS} at a time, so I ranked the first "
            f"{tasks_mod.MAX_TASKS} and left out: {shown}{more}._")
    return (note,)


def _with_tasks(parsed: tasks_mod.Parsed, kept: tuple[str, ...]) -> tasks_mod.Parsed:
    return tasks_mod.Parsed(kept, parsed.goals, parsed.done, parsed.merged, parsed.instruction)


def _notes(parsed: tasks_mod.Parsed) -> tuple[str, ...]:
    notes = []
    if parsed.done:
        notes.append(f"{parsed.done} done item{'s' if parsed.done != 1 else ''} skipped.")
    if parsed.merged:
        notes.append(f"{parsed.merged} duplicate{'s' if parsed.merged != 1 else ''} merged.")
    return tuple(notes)
