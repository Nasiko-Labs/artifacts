"""The approve → write walk for one proposal, its state on the A2A task between pauses.

    start → [input-required: Post / Skip / typed text] → send_one ─┬─ written / not / blocked
                                                                                         └─ gateway ask → [auth-required]
                                                                                              → authorized: same call again
                                                                                              → anything else: denied
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from deps import TurnDeps
from send_guard import Approval
from sender import SendResult, resume_send, send_one
from writes import Proposal, approval_for, copyable, describe, interpret, question

log = logging.getLogger("pr_reviewer.walk")

METADATA_KEY = "pr_review_write"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."


@dataclass(frozen=True)
class Walk:
    proposal: Proposal
    stage: str = "approve"              # "approve" (our question) | "gateway" (platform card)
    approval: Approval | None = None
    loop: dict | None = None
    done: bool = False

    def to_metadata(self) -> dict:
        return {"proposal": self.proposal.to_dict(), "stage": self.stage,
                "approval": self.approval.to_dict() if self.approval else None, "loop": self.loop, "done": self.done}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        return Walk(proposal=Proposal.from_dict(data.get("proposal") or {}), stage=str(data.get("stage") or "approve"),
                    approval=Approval.from_dict(data.get("approval")), loop=data.get("loop") or None,
                    done=bool(data.get("done")))


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata: the walk, to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    outcomes: dict | None = None  # write key → sent / skipped / denied / blocked / not_sent
    envelope: dict | None = None  # the review envelope, sent as a DataPart next to the text


def start(proposal: Proposal, preamble: str = "") -> Outcome:
    text, hitl = question(proposal)
    return Outcome("input_required", "\n\n".join(p for p in (preamble, text) if p),
                   {METADATA_KEY: Walk(proposal).to_metadata()}, hitl)


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping) or not stored.get("proposal"):
        return Outcome("complete", NOT_WAITING)
    walk = Walk.from_metadata(stored)
    if walk.done:
        return Outcome("complete", NOT_WAITING)
    if walk.stage == "gateway":
        if walk.approval is None or not walk.loop:  # fail closed: never guess what was approved
            return _finish(walk, "not_sent", "Not written: the pause state was incomplete.")
        return _after(walk, walk.approval, await resume_send(deps, walk.proposal, walk.approval, walk.loop, answer))
    decision, typed = interpret(answer)
    if decision == "skip":
        return _finish(walk, "skipped", "Skipped; nothing was written.")
    approval = approval_for(walk.proposal, typed or walk.proposal.body)
    log.info("%s approved (%s)", walk.proposal.kind, decision)
    return _after(walk, approval, await send_one(deps, walk.proposal, approval))


def _after(walk: Walk, approval: Approval, result: SendResult) -> Outcome:
    p = walk.proposal
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval, loop=result.loop)
        text = "The platform needs your approval before this is written:\n\n" + describe(p, approval.draft)
        return Outcome("auth_required", text, {METADATA_KEY: paused.to_metadata()},
                       {"hitl_request_id": result.hitl_request_id})
    if result.status == "sent":
        return _finish(walk, "sent", f"Done: commented on {p.destination}.")
    if result.status == "denied":
        return _finish(walk, "denied", "The write was denied, so nothing was written and I won't retry it.")
    reason = ("Writing is blocked for this agent." if result.status == "blocked"
              else f"Not written: {result.reason or 'unknown reason'}.")
    return _finish(walk, result.status, copyable(reason, approval.draft))


def _finish(walk: Walk, status: str, text: str) -> Outcome:
    done = replace(walk, done=True, stage="approve", approval=None, loop=None)
    return Outcome("complete", text, {METADATA_KEY: done.to_metadata()}, None, {walk.proposal.key: status})
