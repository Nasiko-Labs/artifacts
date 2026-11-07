"""One turn, decided in code before any model call.

    greeting / small talk                       -> canned intro (0 calls)
    a table (pasted, file or envelope) ─┬─ a column missing -> pause: which column
                                        ├─ < 2 line items   -> fixed reply (0 calls)
                                        └─ otherwise        -> narrate (<= 2 calls)
    a table stored ─┬─ "add driver: ..."  -> bind in code, re-narrate (<= 2 calls)
                    ├─ a re-render command -> template (0 calls) or "explain again" (<= 2 calls)
                    └─ anything else      -> one scoped question (<= 2 calls)
    nothing stored ─┬─ asks for a narrative -> pause: paste the table
                    ├─ a question           -> one scoped call (declined or "need a table")
                    └─ otherwise            -> "I need a table" (0 calls)
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import drivers
import followup
import hitl
import inputs
import prompts
import request
import sign
import smalltalk
from amounts import symbol_in
from asking import ask
from llm import TurnDeps
from present import View, present, rows_to_state
from table import Table, parse_table

MIN_ITEMS = 2
_WANTS_NARRATIVE = re.compile(r"\b(?:variance|budget|actual|narrat\w*|explain|flux|bva|b ?vs ?a)\b", re.IGNORECASE)
_QUESTION = re.compile(r"\?\s*$|^(?:what|who|why|how|when|where|which|is|are|can|could|should|would|will|tell|write|"
                       r"compute|calculate|give|translate)\b", re.IGNORECASE)


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: inputs.Incoming, answer: hitl.Answer | None = None,
                 pending: hitl.Pending | None = None):
        self.deps, self.incoming, self.answer, self.pending = deps, incoming, answer, pending


def _reply(text: str, notes: tuple[str, ...] = ()) -> dict:
    return {"reply": "\n\n".join(p for p in (text, *notes) if p)}


async def handle(state: Mapping[str, Any], scope: Scope) -> dict:
    if scope.answer is not None:
        if scope.answer.action == "cancel":
            return _reply(prompts.CANCELLED)
        wants = scope.incoming.wants_json or bool(scope.pending and scope.pending.wants_json)
        return await _new_input(scope.answer.text, (), scope, dict(scope.answer.forced), resumed=True, wants=wants)
    incoming = scope.incoming
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return _reply(canned, incoming.notes)
    text, notes = inputs.combined(incoming)
    if inputs.envelope_rows(text) is not None or parse_table(text) is not None:
        return await _new_input(text, notes, scope, {}, resumed=False, wants=incoming.wants_json)
    if state.get("rows"):
        return await _on_stored(state, scope, notes)
    if _WANTS_NARRATIVE.search(text) and not _QUESTION.search(text.strip()):
        pending = hitl.Pending(hitl.NEED_TABLE, instruction=prompts.strip_markers(text)[:300],
                               wants_json=incoming.wants_json)
        return {"pause": pending.to_metadata()}
    if _QUESTION.search(text.strip()):
        return _reply(await ask(scope.deps, text, None, incoming.policy), notes)
    return _reply(smalltalk.NEED_TABLE_REPLY, notes)


def _read(text: str, forced: dict[str, int]) -> Table | None:
    rows = inputs.envelope_rows(text)
    return Table(rows=rows) if rows is not None else parse_table(text, forced)


async def _new_input(text: str, notes: tuple[str, ...], scope: Scope, forced: dict[str, int], resumed: bool,
                     wants: bool) -> dict:
    clean = prompts.strip_markers(text)
    table = _read(clean, forced)
    if table is not None and table.missing:
        pending = hitl.Pending(hitl.MISSING_COLUMN, text=clean, role=table.missing, columns=table.columns,
                               forced=dict(table.mapping), wants_json=wants)
        return {"pause": pending.to_metadata()}
    if table is None or len(table.items) < MIN_ITEMS:
        return _reply(smalltalk.NEED_TABLE_REPLY, notes)
    if not scope.deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT)
    state = _table_state(table, clean, notes)
    view = View(wants_json=wants, instruction=state["instruction"], notes=notes, policy=scope.incoming.policy)
    outcome = await present(state, scope.deps, view)
    if outcome.off_topic:
        return _reply(outcome.reply)
    return {**state, "reply": outcome.reply, "data": outcome.data}


def _table_state(table: Table, text: str, notes: tuple[str, ...]) -> dict:
    names = [r.name for r in table.items]
    outside = table.outside if table.columns else ""
    instruction = " ".join(s for s in re.split(r"(?<=[.!?:])\s+|\n+", outside)
                           if s.strip() and not drivers.mentions(s, names)).strip()[:600]
    wanted = request.settings_of(instruction)
    mode, stated = sign.mode_from(instruction)
    settings = {"length": wanted.length or "default", "threshold": None if wanted.threshold is None else str(wanted.threshold),
                "period": wanted.period, "only": wanted.only or "", "sign_mode": mode, "sign_stated": stated}
    coverage = {"rows": table.data_rows, "unparseable": table.unparseable, "skipped": len(table.skipped),
                "capped": table.capped, "blanks": table.blanks}
    return {"rows": rows_to_state(table.rows), "symbol": table.symbol or symbol_in(instruction), "message": outside,
            "source": text, "instruction": instruction, "settings": settings, "added": {}, "coverage": coverage}


async def _on_stored(state: Mapping[str, Any], scope: Scope, notes: tuple[str, ...]) -> dict:
    text, deps = scope.incoming.text, scope.deps
    names = [r["name"] for r in state["rows"] if not r.get("is_total")]
    add = followup.parse_add_driver(text, names)
    if add is not None:
        if add.candidates:
            listed = ", ".join(add.candidates)
            return _reply(f"That matches several lines ({listed}). Say which one, e.g. "
                          f"'add driver: {add.candidates[0]}: <the reason>'.")
        if not add.name:
            return _reply(prompts.NO_MATCH_DRIVER)
        updated = {**state, "added": {**(state.get("added") or {}), add.name: add.text}}
        return await _show(updated, scope, View(instruction=state.get("instruction") or "", notes=notes,
                                                wants_json=scope.incoming.wants_json, policy=scope.incoming.policy))
    command = followup.parse_command(text)
    if command is None:
        return _reply(await ask(deps, text, state, scope.incoming.policy), notes)
    s = command.settings
    settings = dict(state.get("settings") or {})
    settings.update({k: v for k, v in (("length", s.length), ("only", s.only),
                                       ("threshold", None if s.threshold is None else str(s.threshold))) if v is not None})
    view = View(use_model=command.renarrate, table_only=command.table_only, notes=notes,
                wants_json=s.wants_json or scope.incoming.wants_json, instruction=state.get("instruction") or "",
                policy=scope.incoming.policy)
    return await _show({**state, "settings": settings}, scope, view)


async def _show(state: dict, scope: Scope, view: View) -> dict:
    if view.use_model and not scope.deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT)
    outcome = await present(state, scope.deps, view)
    if outcome.off_topic:
        return _reply(outcome.reply)
    return {**state, "reply": outcome.reply, "data": outcome.data}
