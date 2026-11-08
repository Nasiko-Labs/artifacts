"""The approve → send walk: one draft per pause, its state on the A2A task between pauses.

    start → [input-required: Send / Skip / edited text] → send_one ─┬─ sent / not sent / blocked → next
                                                                    └─ gateway ask → [auth-required]
                                                                         → authorized: re-run the same call
                                                                         → anything else: denied, never retried

(Copied in once from the hardened kit; owned by this agent.)
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import render
from deps import TurnDeps
from drafts import Draft, approval_for, approval_question, describe, interpret_answer
from send_guard import Approval
from sender import SendResult, resume_send, send_one

log = logging.getLogger("refund_dispute.walk")

METADATA_KEY = "refund_dispute.walk"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."


@dataclass(frozen=True)
class Walk:
    drafts: tuple[Draft, ...]
    index: int = 0
    results: tuple[tuple[str, str], ...] = ()   # (draft key, sent/skipped/denied/blocked/not_sent)
    notes: tuple[str, ...] = ()
    stage: str = "approve"                       # "approve" (our question) | "gateway" (platform card)
    approval: Approval | None = None
    loop: dict | None = None
    preamble: str = ""

    @property
    def current(self) -> Draft:
        return self.drafts[self.index]

    def to_metadata(self) -> dict:
        return {"drafts": [d.to_dict() for d in self.drafts], "index": self.index,
                "results": [list(r) for r in self.results], "notes": list(self.notes), "stage": self.stage,
                "approval": self.approval.to_dict() if self.approval else None, "loop": self.loop,
                "preamble": self.preamble}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Walk(drafts=tuple(Draft.from_dict(d) for d in data.get("drafts") or ()),
                    index=int(data.get("index") or 0),
                    results=tuple((str(a), str(b)) for a, b in data.get("results") or ()),
                    notes=tuple(str(n) for n in data.get("notes") or ()), stage=str(data.get("stage") or "approve"),
                    approval=Approval.from_dict(data.get("approval")), loop=data.get("loop") or None,
                    preamble=str(data.get("preamble") or ""))


@dataclass(frozen=True)
class Outcome:
    kind: str                    # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata: the walk, to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    outcomes: dict | None = None  # draft key → result so far, for the conversation
    envelope: dict | None = None  # the list's envelope, sent as a DataPart


async def start(deps: TurnDeps, drafts: Sequence[Draft], preamble: str = "") -> Outcome:
    return _ask(Walk(drafts=tuple(drafts), preamble=preamble))


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping):
        return Outcome("complete", NOT_WAITING)
    walk = Walk.from_metadata(stored)
    if walk.index >= len(walk.drafts):
        return Outcome("complete", NOT_WAITING)
    if walk.stage == "gateway":
        if walk.approval is None or not walk.loop:  # fail closed: never guess what was approved
            return _advance(walk, "not_sent", f"{walk.current.label}: not sent (the pause state was incomplete).")
        result = await resume_send(deps, walk.current, walk.approval, walk.loop, answer)
        return _after_send(walk, walk.approval, result)
    decision, edited = interpret_answer(answer)
    if decision == "skip":
        return _advance(walk, "skipped", f"{walk.current.label}: skipped, nothing sent.")
    approval = approval_for(walk.current, edited or None)
    log.info("draft %s approved (%s)", walk.current.label, decision)
    return _after_send(walk, approval, await send_one(deps, walk.current, approval))


def _ask(walk: Walk) -> Outcome:
    question, hitl = approval_question(walk.current)
    lead = walk.notes[-1] if walk.notes else walk.preamble
    progress = f"Draft {walk.index + 1} of {len(walk.drafts)}." if len(walk.drafts) > 1 else ""
    text = "\n\n".join(part for part in (lead, progress, question) if part)
    return Outcome("input_required", text, _metadata(walk), hitl, dict(walk.results) or None)


def _after_send(walk: Walk, approval: Approval, result: SendResult) -> Outcome:
    draft = walk.current
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval, loop=result.loop)
        text = "The platform needs your approval before this is sent:\n\n" + describe(draft, approval.draft)
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id},
                       dict(walk.results) or None)
    return _advance(walk, result.status, _note(draft, approval, result))


def _note(draft: Draft, approval: Approval, result: SendResult) -> str:
    who = draft.who
    if result.status == "sent":
        return f"{draft.label}: sent to {who}."
    if result.status == "denied":
        return f"{draft.label}: the send was denied, so nothing was sent and I won't retry it."
    reason = ("Sending is blocked for this agent." if result.status == "blocked"
              else f"Not sent: {result.reason or 'unknown reason'}.")
    return render.render_copyable(draft.label, who, approval.subject, approval.draft, reason)


def _advance(walk: Walk, status: str, note: str) -> Outcome:
    moved = replace(walk, index=walk.index + 1, results=(*walk.results, (walk.current.key, status)),
                    notes=(*walk.notes, note), stage="approve", approval=None, loop=None)
    if moved.index < len(moved.drafts):
        return _ask(moved)
    return Outcome("complete", "\n\n".join(moved.notes), _metadata(moved), None, dict(moved.results))


def _metadata(walk: Walk) -> dict:
    return {METADATA_KEY: walk.to_metadata()}
