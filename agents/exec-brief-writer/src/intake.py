"""Pure: decide what a turn does, in code, before any model call.

Returns a partial graph-state update whose `route` is "compose" (with `work` for the compose node)
or "" (the turn ends here: a fixed reply, a code-only edit, or a pause with `pause` set)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import brief as b
import commands
import pauses
import prompts
import render
import sections as sec
from inputs import MAX_TOTAL_CHARS, split_instruction, words

REVISION_JOIN = "\nRevision request: "


def decide(state: Mapping[str, Any]) -> dict:
    resume = state.get("resume") or ""
    if resume:
        return _resume(state, resume, state.get("query") or "")
    return _fresh(state, state.get("query") or "")


def _fresh(state: Mapping[str, Any], query: str) -> dict:
    stored = sec.from_state(state.get("sections"))
    files = sec.from_state(state.get("files"))
    command = commands.parse(query)
    if command.kind and not files:
        return _command(state, command, stored)
    instruction, source = split_instruction(query)
    pasted = sec.number((*sec.split(source), *files)) if (source or files) else ()
    if pasted:
        if not stored or commands.wants_new(instruction):
            return _prepare(pasted, instruction, revise=False)
        if commands.wants_add(instruction):
            return _prepare(sec.append(stored, pasted), _joined(state.get("instruction"), instruction), revise=False)
        return _pause(pauses.ADD_OR_NEW, pending=pasted, pending_instruction=instruction)
    if stored:
        joined = _joined(state.get("instruction"), query, REVISION_JOIN)
        return _work(stored, joined, revise=True, keep_instruction=state.get("instruction") or "",
                     followup=query.strip())
    if words(query) and commands.asks_for_brief(query):
        return _pause(pauses.NOTHING_PASTED)
    return _work((), query, revise=False, scope_only=True)


def _resume(state: Mapping[str, Any], kind: str, answer: str) -> dict:
    choice = pauses.read_answer(kind, answer)
    if kind == pauses.NOTHING_PASTED:
        return _fresh(state, answer) if choice == "paste" else _end(prompts.ENDED)
    pending = sec.from_state(state.get("pending"))
    if choice == "cancel":
        return _end(prompts.CANCELLED)
    if not pending:
        return _end(prompts.LOST_PAUSE)
    instruction = state.get("pending_instruction") or ""
    if kind == pauses.ADD_OR_NEW:
        stored = sec.from_state(state.get("sections"))
        if choice == "add":
            return _prepare(sec.append(stored, pending), _joined(state.get("instruction"), instruction), revise=False)
        return _prepare(sec.number(pending), instruction, revise=False)
    kept = sec.first_that_fit(pending, MAX_TOTAL_CHARS)
    note = prompts.CUT_TO_FIT.format(kept=len(kept), total=len(pending))
    return _work(kept, instruction, revise=False, notes=[note])


def _command(state: Mapping[str, Any], command: commands.Command, stored: Sequence[sec.Section]) -> dict:
    if command.kind == "start_over":
        return {**_end(prompts.CLEARED), "sections": [], "brief": None, "candidates": None, "instruction": ""}
    current = b.from_dict(state.get("brief"))
    if current is None or not stored:
        return _end(prompts.NO_BRIEF_YET)
    if command.kind == "show_sources":
        return _end(render.render(current, stored, sources=True))
    edited, problem = commands.apply(current, command)
    if problem:
        return _end(problem)
    return {**_end(render.render(edited, stored)), "brief": b.to_dict(edited)}


def _prepare(final: Sequence[sec.Section], instruction: str, *, revise: bool) -> dict:
    if sec.total_chars(final) > MAX_TOTAL_CHARS:
        return _pause(pauses.OVERSIZE, pending=final, pending_instruction=instruction)
    return _work(final, instruction, revise=revise)


def _work(final: Sequence[sec.Section], instruction: str, *, revise: bool, scope_only: bool = False,
          notes: Sequence[str] = (), keep_instruction: str | None = None, followup: str = "") -> dict:
    """`followup`: a free-text message on the stored brief, scope-checked against it before revising."""
    work = {"sections": sec.to_state(final), "instruction": instruction, "revise": revise, "scope_only": scope_only,
            "notes": list(notes), "store_instruction": instruction if keep_instruction is None else keep_instruction,
            "followup": followup}
    return {"route": "compose", "work": work, "pending": [], "pending_instruction": ""}


def _pause(kind: str, *, pending: Sequence[sec.Section] = (), pending_instruction: str = "") -> dict:
    text, _ = pauses.question(kind)
    return {"route": "", "reply": text, "pause": kind, "pending": sec.to_state(pending),
            "pending_instruction": pending_instruction}


def _end(reply: str) -> dict:
    return {"route": "", "reply": reply, "pending": [], "pending_instruction": ""}


def _joined(earlier: str | None, latest: str, sep: str = "\n") -> str:
    parts = [p.strip() for p in (earlier or "", latest or "") if p and p.strip()]
    return sep.join(parts)
