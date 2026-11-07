"""The approve → write walk for one rollup, its state on the A2A task between pauses.

    start → [input-required: Write / Skip / edited text] → write_one ─┬─ written / not written / blocked → done
                                                                      └─ gateway ask → [auth-required]
                                                                           → authorized: re-run the same call
                                                                           → anything else: denied, never retried
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import render
from approval import (
    WriteRequest,
    approval_for,
    approval_question,
    describe,
    interpret_answer,
)
from deps import TurnDeps
from send_guard import Approval
from writer import WriteResult, resume_write, write_one

log = logging.getLogger("commitment_audit.walk")

METADATA_KEY = "commitment_audit"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata: the pause, to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    written: tuple[str, str] | None = None  # (target key, final status), for the conversation's state


async def start(request: WriteRequest, preamble: str = "") -> Outcome:
    question, hitl = approval_question(request)
    text = "\n\n".join(part for part in (preamble, question) if part)
    return Outcome("input_required", text, _metadata(request, "approve"), hitl)


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping) or not isinstance(stored.get("request"), Mapping):
        return Outcome("complete", NOT_WAITING)
    request = WriteRequest.from_dict(stored["request"])
    if stored.get("stage") == "gateway":
        approval = Approval.from_dict(stored.get("approval"))
        if approval is None or not stored.get("loop"):  # fail closed: never guess what was approved
            return _done(request, "not_written", "Not written: the pause state was incomplete.")
        result = await resume_write(deps, request, approval, stored["loop"], answer)
        return _after_write(request, approval, result)
    decision, edited = interpret_answer(answer)
    if decision == "skip":
        return _done(request, "skipped", "Skipped: nothing was written.")
    approval = approval_for(request, edited or None)
    log.info("rollup write approved (%s)", decision)
    return _after_write(request, approval, await write_one(deps, request, approval))


def _after_write(request: WriteRequest, approval: Approval, result: WriteResult) -> Outcome:
    if result.status == "paused":
        text = "The platform needs your approval before this is written:\n\n" + describe(request, approval.draft)
        metadata = _metadata(request, "gateway", approval, result.loop)
        return Outcome("auth_required", text, metadata, {"hitl_request_id": result.hitl_request_id})
    if result.status == "written":
        return _done(request, "written", f"Saved the rollup to {request.where}.")
    if result.status == "denied":
        return _done(request, "denied", "The write was denied, so nothing was written and I won't retry it.")
    reason = ("Writing is blocked for this agent." if result.status == "blocked"
              else f"Not written: {result.reason or 'unknown reason'}.")
    return _done(request, result.status, render.render_copyable(reason, approval.draft))


def _done(request: WriteRequest, status: str, text: str) -> Outcome:
    return Outcome("complete", text, None, None, (request.target_key, status))


def _metadata(request: WriteRequest, stage: str, approval: Approval | None = None, loop: dict | None = None) -> dict:
    return {METADATA_KEY: {"request": request.to_dict(), "stage": stage,
                           "approval": approval.to_dict() if approval else None, "loop": loop}}
