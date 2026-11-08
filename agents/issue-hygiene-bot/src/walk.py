"""The approve → send walk: one owner's message per pause, its state on the A2A task between pauses.

    start → [input-required: Send / Skip / Cancel / edited text] → send_one ─┬─ sent / not sent / blocked → next
                                                                             └─ gateway ask → [auth-required]
                                                                                  → authorized: re-run the same call
                                                                                  → anything else: denied
An edited message that lost the honesty line or an issue link is shown once more with a warning. Cancel stops
the walk; owners not reached yet are reported. There is no "send all remaining" option."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import dm as dm_text
import render
import replies
from deps import TurnDeps
from dm import Dm
from nasiko_hitl import MAX_QUESTION_HEADER_LEN, question_metadata
from pick import PHASE_KEY
from send_guard import Approval
from sender import SendResult, approval_for, resume_send, send_one

log = logging.getLogger("issue_hygiene.walk")

SEND, SKIP, CANCEL = "Send", "Skip", "Cancel"
_SEND_WORDS = frozenset({"send", "yes", "y", "ok", "okay", "approve", "approved", "send it"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "don't send", "dont send", "denied", "deny", "reject", "decline"})
_CANCEL_WORDS = frozenset({"cancel", "stop", "cancel all", "stop all", "abort", "end"})


@dataclass(frozen=True)
class Walk:
    dms: tuple[Dm, ...]
    index: int = 0
    results: tuple[tuple[str, str], ...] = ()   # (owner key, sent/skipped/denied/blocked/not_sent/not_reached)
    notes: tuple[str, ...] = ()
    stage: str = "approve"                       # "approve" (our card) | "gateway" (the platform's card)
    approval: Approval | None = None
    loop: dict | None = None
    preamble: str = ""

    @property
    def current(self) -> Dm:
        return self.dms[self.index]

    def to_metadata(self) -> dict:
        return {"phase": "walk", "dms": [d.to_dict() for d in self.dms], "index": self.index,
                "results": [list(r) for r in self.results], "notes": list(self.notes), "stage": self.stage,
                "approval": self.approval.to_dict() if self.approval else None, "loop": self.loop,
                "preamble": self.preamble}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Walk(dms=tuple(Dm.from_dict(d) for d in data.get("dms") or ()), index=int(data.get("index") or 0),
                    results=tuple((str(a), str(b)) for a, b in data.get("results") or ()),
                    notes=tuple(str(n) for n in data.get("notes") or ()), stage=str(data.get("stage") or "approve"),
                    approval=Approval.from_dict(data.get("approval")), loop=data.get("loop") or None,
                    preamble=str(data.get("preamble") or ""))


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    outcomes: dict | None = None  # owner key → result so far
    data: dict | None = None      # the structured envelope, sent as a data part


def start(dms: Sequence[Dm], preamble: str = "") -> Outcome:
    return _ask(Walk(dms=tuple(dms), preamble=preamble))


async def resume(deps: TurnDeps, stored: Mapping[str, Any], answer: str) -> Outcome:
    walk = Walk.from_metadata(stored)
    if walk.index >= len(walk.dms):
        return Outcome("complete", replies.NOT_WAITING)
    if walk.stage == "gateway":
        if walk.approval is None or not walk.loop:  # fail closed: never guess what was approved
            return _advance(walk, "not_sent", f"{walk.current.owner_name}: not sent (the pause state was incomplete).")
        return _after_send(walk, walk.approval, await resume_send(deps, walk.approval, walk.loop, answer))
    decision, edited = interpret_answer(answer)
    current = walk.current
    if decision == "cancel":
        return _cancel(walk)
    if decision == "skip":
        return _advance(walk, "skipped", f"{current.owner_name}: skipped, nothing sent.")
    if decision == "edit":
        problems = dm_text.edit_problems(edited, current, deps.config)
        if problems and not (current.warnings and edited == current.body):
            log.info("edited message for %s lost a required part; showing it once more", current.owner_key)
            changed = replace(current, body=edited, warnings=problems)
            return _ask(replace(walk, dms=(*walk.dms[:walk.index], changed, *walk.dms[walk.index + 1:])))
    approval = approval_for(current, edited or None)
    log.info("message for %s approved (%s)", current.owner_key, decision)
    return _after_send(walk, approval, await send_one(deps, current, approval))


def interpret_answer(answer: str) -> tuple[str, str]:
    """send / skip / cancel / edit. Empty is skip: nothing goes out by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _SKIP_WORDS:
        return "skip", ""
    if word in _CANCEL_WORDS:
        return "cancel", ""
    if word in _SEND_WORDS:
        return "send", ""
    return "edit", text


def approval_question(message: Dm) -> tuple[str, dict]:
    header = f"Send this message to {message.owner_name}?"
    if len(header.encode("utf-8")) > MAX_QUESTION_HEADER_LEN:
        header = "Send this message?"
    hitl = question_metadata(header, [(SEND, "Send exactly this text as a direct message."),
                                      (SKIP, "Don't send it; move on to the next owner."),
                                      (CANCEL, "Stop here; send nothing more.")],
                             multi_select=False, allow_custom_input=True)
    hint = f"Reply {SEND}, {SKIP} or {CANCEL}, or type your own text to send instead."
    return f"{describe(message, message.body)}\n\n{hint}", hitl


def describe(message: Dm, body: str) -> str:
    lines = [f"Direct message to {message.owner_name} (chat user {message.user_id})", f"Via: {message.tool}"]
    warning = [f"Warning: {'; '.join(message.warnings)}. Send it anyway, or type a corrected text."] \
        if message.warnings else []
    return "\n".join([*lines, *warning, "", "```", body, "```"])


def _ask(walk: Walk) -> Outcome:
    question, hitl = approval_question(walk.current)
    lead = walk.notes[-1] if walk.notes else walk.preamble
    progress = f"Message {walk.index + 1} of {len(walk.dms)}." if len(walk.dms) > 1 else ""
    text = "\n\n".join(part for part in (lead, progress, question) if part)
    return Outcome("input_required", text, _metadata(walk), hitl, dict(walk.results) or None)


def _after_send(walk: Walk, approval: Approval, result: SendResult) -> Outcome:
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval, loop=result.loop)
        text = "The platform needs your approval before this is sent:\n\n" + describe(walk.current, approval.draft)
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id},
                       dict(walk.results) or None)
    return _advance(walk, result.status, _note(walk.current, approval, result))


def _note(message: Dm, approval: Approval, result: SendResult) -> str:
    if result.status == "sent":
        return f"{message.owner_name}: message sent."
    if result.status == "denied":
        return f"{message.owner_name}: the send was denied, so nothing was sent and I won't retry it."
    reason = ("Sending is blocked for this agent." if result.status == "blocked"
              else f"Not sent: {result.reason or 'unknown reason'}.")
    return dm_text.copyable(replace(message, body=approval.draft), reason)


def _advance(walk: Walk, status: str, note: str) -> Outcome:
    moved = replace(walk, index=walk.index + 1, results=(*walk.results, (walk.current.owner_key, status)),
                    notes=(*walk.notes, note), stage="approve", approval=None, loop=None)
    return _ask(moved) if moved.index < len(moved.dms) else _finish(moved)


def _cancel(walk: Walk) -> Outcome:
    rest = tuple((d.owner_key, "not_reached") for d in walk.dms[walk.index:])
    return _finish(replace(walk, index=len(walk.dms), results=(*walk.results, *rest), stage="approve",
                           approval=None, loop=None))


def _finish(walk: Walk) -> Outcome:
    names = {d.owner_key: d.owner_name for d in walk.dms}
    text = render.walk_report(dict(walk.results), names, walk.notes)
    return Outcome("complete", text, _metadata(walk), None, dict(walk.results))


def _metadata(walk: Walk) -> dict:
    return {PHASE_KEY: walk.to_metadata()}
