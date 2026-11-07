"""The approve → write walk, its state on the A2A task between pauses. One card per nudge: "nudge all
stale" queues up to five proposals and shows them one after another, each with its own approval.

    start → [input-required: Post / Post and mention / Skip / typed text] → send_one ─┬─ written / not / blocked
                                                                                       └─ gateway ask → [auth-required]
                                                                                            → authorized: same call again
    after each settled nudge: the next queued card, or complete.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from deps import TurnDeps
from nudge import Proposal, copyable
from send import SendResult, resume_send, send_one
from send_guard import Approval
from writes import approval_for, describe, interpret, question

log = logging.getLogger("pr_review_queue.walk")

METADATA_KEY = "pr_review_queue"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."


@dataclass(frozen=True)
class Walk:
    proposal: Proposal
    queue: tuple[Proposal, ...] = ()
    stage: str = "approve"              # "approve" (our card) | "gateway" (the platform's card)
    approval: Approval | None = None
    args: dict | None = None
    done: bool = False
    total: int = 1

    def to_metadata(self) -> dict:
        return {"proposal": self.proposal.to_dict(), "queue": [p.to_dict() for p in self.queue],
                "stage": self.stage, "approval": self.approval.to_dict() if self.approval else None,
                # JSON text: a protobuf Struct would turn the integer pull request number into a float.
                "args": json.dumps(self.args) if self.args else None, "done": self.done, "total": self.total}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        return Walk(proposal=Proposal.from_dict(data.get("proposal") or {}),
                    queue=tuple(Proposal.from_dict(p) for p in data.get("queue") or ()),
                    stage=str(data.get("stage") or "approve"), approval=Approval.from_dict(data.get("approval")),
                    args=json.loads(data["args"]) if isinstance(data.get("args"), str) else None,
                    done=bool(data.get("done")), total=int(data.get("total") or 1))

    @property
    def position(self) -> str:
        return f"{self.total - len(self.queue)} of {self.total}" if self.total > 1 else ""


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata: the walk, to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    outcomes: dict | None = None  # write key → sent / skipped / denied / blocked / not_sent


def start(proposals: Sequence[Proposal], preamble: str = "") -> Outcome:
    first, *rest = proposals
    return _ask(Walk(first, tuple(rest), total=len(proposals)), preamble, {})


def _ask(walk: Walk, preamble: str, outcomes: dict) -> Outcome:
    text, hitl = question(walk.proposal, walk.position)
    return Outcome("input_required", "\n\n".join(p for p in (preamble, text) if p),
                   {METADATA_KEY: walk.to_metadata()}, hitl, outcomes or None)


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping) or not stored.get("proposal"):
        return Outcome("complete", NOT_WAITING)
    walk = Walk.from_metadata(stored)
    if walk.done:
        return Outcome("complete", NOT_WAITING)
    if walk.stage == "gateway":
        if walk.approval is None or not walk.args:  # fail closed: never guess what was approved
            return _settle(walk, "not_sent", "Not posted: the pause state was incomplete.")
        return _after(walk, walk.approval, await resume_send(deps, walk.proposal, walk.approval, walk.args, answer))
    decision, body = interpret(answer, walk.proposal)
    if decision == "skip":
        return _settle(walk, "skipped", f"Skipped {walk.proposal.destination}; nothing was posted.")
    approval = approval_for(walk.proposal, body)
    log.info("nudge approved (%s)", decision)
    return _after(walk, approval, await send_one(deps, walk.proposal, approval))


def _after(walk: Walk, approval: Approval, result: SendResult) -> Outcome:
    p = walk.proposal
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval, args=result.args)
        text = "The platform needs your approval before this is posted:\n\n" + describe(p, approval.draft)
        return Outcome("auth_required", text, {METADATA_KEY: paused.to_metadata()},
                       {"hitl_request_id": result.hitl_request_id})
    if result.status == "sent":
        link = f": {result.url}" if result.url else "."
        return _settle(walk, "sent", f"Posted the nudge on {p.destination}{link}")
    if result.status == "denied":
        return _settle(walk, "denied", "The comment was denied, so nothing was posted and I won't retry it.")
    reason = ("Commenting is blocked for this agent." if result.status == "blocked"
              else f"Not posted: {(result.reason or 'unknown reason').rstrip('.')}.")
    return _settle(walk, result.status, copyable(reason, approval.draft))


def _settle(walk: Walk, status: str, text: str) -> Outcome:
    outcomes = {walk.proposal.key: status}
    if walk.queue:
        nxt = Walk(walk.queue[0], walk.queue[1:], total=walk.total)
        return _ask(nxt, text, outcomes)
    done = replace(walk, done=True, stage="approve", approval=None, args=None)
    return Outcome("complete", text, {METADATA_KEY: done.to_metadata()}, None, outcomes)
