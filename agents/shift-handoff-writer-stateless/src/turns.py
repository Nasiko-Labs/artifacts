"""The work behind the chain's steps: build a handoff, run a follow-up on one rebuilt from the chat history,
or check scope. Each returns this turn's reply and the envelope for the data part; nothing is stored, so a
follow-up rebuilds the handoff from the recalled notes and replays the recalled changes first."""

from __future__ import annotations

import logging
import re
from typing import Any

import handoff as hf
import pipeline
import prompts
import render
import smalltalk
from entries import parse, ref_key
from followup import Command
from inputs import Incoming
from intake import Plan
from llm import LlmError, TurnDeps, complete_json
from recall import Prior

log = logging.getLogger("shift_handoff.turns")

_SHORT = re.compile(r"\bshort(?:er)?\b|\bbrief\b|\bonly (?:the )?(?:hot|urgent)\b", re.IGNORECASE)
_HOT_ONLY = re.compile(r"\bonly (?:the )?(?:hot|urgent)\b", re.IGNORECASE)
FAILED = {"off_topic": smalltalk.OFF_TOPIC_REPLY, "not_notes": prompts.NOT_NOTES, "failed": prompts.MODEL_FAILED,
          "nothing": prompts.NOTHING_BACKED}


def with_notes(text: str, notes: tuple[str, ...] = ()) -> str:
    return "\n\n".join(part for part in (text, *(f"_{n}_" for n in notes)) if part)


def _view_for(instruction: str) -> str:
    if _HOT_ONLY.search(instruction or ""):
        return "hot"
    return "short" if _SHORT.search(instruction or "") else "default"


def _reply(handoff: hf.Handoff, incoming: Incoming, view: str = "default", lead: str = "",
           extra: tuple[str, ...] = ()) -> dict:
    markdown = render.render(handoff, view, extra)
    envelope = hf.envelope(handoff, markdown)
    text = render.as_json(envelope) if incoming.wants_json else markdown
    return {"reply": "\n\n".join(p for p in (lead, text) if p), "data": envelope}


async def build(deps: TurnDeps, plan: Plan, incoming: Incoming) -> dict:
    entries = parse(plan.notes)
    if not entries:
        return {"reply": smalltalk.TOO_SHORT_REPLY}
    outcome = await pipeline.build(deps, entries, plan.instruction, incoming.policy, deps.now())
    if outcome.status != "ok":
        return {"reply": with_notes(FAILED[outcome.status], plan.extra)}
    return _reply(outcome.handoff, incoming, _view_for(plan.instruction), extra=plan.extra)


async def _rebuilt(deps: TurnDeps, prior: Prior, incoming: Incoming) -> hf.Handoff | str:
    """The handoff again from the recalled notes, with the recalled adds and marks replayed in order."""
    entries = parse(prior.notes)
    outcome = await pipeline.build(deps, entries, prior.instruction, incoming.policy, deps.now()) if entries else None
    if outcome is None or outcome.status != "ok":
        return FAILED[outcome.status] if outcome is not None else prompts.NEED_REPASTE
    handoff = outcome.handoff
    for change in prior.changes:
        if change.kind == "mark":
            handoff = hf.mark_resolved(handoff, _ids_for(handoff, change.refs))
        else:
            added = await pipeline.add_note(deps, handoff, prompts.strip_markers(change.text))
            handoff = added.handoff if added.status == "ok" else handoff
    return handoff


async def followup(deps: TurnDeps, plan: Plan, incoming: Incoming, prior: Prior) -> dict:
    command: Command = plan.command
    if command.kind == "act":
        return {"reply": prompts.NO_ACTION}
    if command.kind == "view" and command.view in render.CUT_VIEWS and prior.reply:
        # a filter of the handoff already shown: zero calls, and the headline cannot change
        return {"reply": render.cut(prior.reply, command.view)}
    if not prior.notes:  # trimmed: only our last handoff is left
        return {"reply": prompts.NEED_REPASTE}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    handoff = await _rebuilt(deps, prior, incoming)
    if isinstance(handoff, str):
        return {"reply": handoff}
    if command.kind == "view":
        return _reply(handoff, incoming, command.view, extra=plan.extra)
    if command.kind == "json":
        return _reply(handoff, Incoming(incoming.text, wants_json=True), extra=plan.extra)
    if command.kind == "mark":
        return _mark(handoff, incoming, command.refs)
    outcome = await pipeline.add_note(deps, handoff, prompts.strip_markers(command.text))
    if outcome.status != "ok":
        return {"reply": FAILED.get(outcome.status, prompts.MODEL_FAILED)}
    return _reply(outcome.handoff, incoming, lead="Added the note.")


def _ids_for(handoff: hf.Handoff, refs: tuple[str, ...]) -> set:
    wanted = set(refs)
    return {i.id for i in handoff.items if wanted & {ref_key(r) for r in i.refs}}


def _mark(handoff: hf.Handoff, incoming: Incoming, refs: tuple[str, ...]) -> dict:
    wanted = set(refs)
    ids = _ids_for(handoff, refs)
    if not ids:
        named = ", ".join(refs)
        return {"reply": f"I don't see {named} in this handoff's items, so nothing changed. {prompts.FOLLOWUP_HELP}"}
    found = list(dict.fromkeys(r for i in handoff.items if i.id in ids for r in i.refs if ref_key(r) in wanted))
    missing = sorted(wanted - {ref_key(r) for r in found})
    lead = f"Marked {', '.join(found)} resolved."
    if missing:
        lead += f" I don't see {', '.join(missing)} in this handoff."
    return _reply(hf.mark_resolved(handoff, ids), incoming, lead=lead)


def _scope_valid(data: Any) -> dict | None:
    if isinstance(data, dict) and data.get("status") in ("in_scope", "off_topic"):
        return {"status": data["status"]}
    return None


async def scope_check(deps: TurnDeps, plan: Plan, incoming: Incoming) -> dict:
    try:
        data = await complete_json(deps, prompts.scope_messages(prompts.strip_markers(incoming.text)[:2_000]),
                                   "status in_scope|off_topic", _scope_valid)
    except LlmError as e:
        log.warning("scope check failed: %s", e)
        return {"reply": prompts.MODEL_FAILED}
    if data["status"] == "off_topic":
        return {"reply": with_notes(smalltalk.OFF_TOPIC_REPLY, plan.extra)}
    reply = prompts.FOLLOWUP_HELP if plan.has_stored else smalltalk.TOO_SHORT_REPLY
    return {"reply": with_notes(reply, plan.extra)}
