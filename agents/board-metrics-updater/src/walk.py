"""The approve -> write walk: one write per pause, its state on the A2A task between pauses.

    start -> [input-required: Add / Skip / typed text] -> send_one -+- written / not written / blocked -> next
                                                                    +- gateway ask -> [auth-required]
                                                                         -> authorized: re-run the same call
                                                                         -> anything else: denied, never retried

A typed answer never changes the row or the paragraph: a number edit is refused, anything else is
answered and the same card is asked again. (Pattern copied from the hardened kit; owned by this agent.)
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import prompts
import render
from deps import TurnDeps
from send_guard import Approval
from sender import SendResult, resume_send, send_one
from writes import (
    Write,
    approval_for,
    approval_question,
    describe,
    interpret_answer,
    is_number_edit,
)

log = logging.getLogger("board_metrics.walk")

METADATA_KEY = "board_metrics.walk"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."


@dataclass(frozen=True)
class Walk:
    writes: tuple[Write, ...]
    index: int = 0
    results: tuple[tuple[str, str], ...] = ()   # (write key, written/skipped/denied/blocked/not_written)
    notes: tuple[str, ...] = ()
    stage: str = "approve"                       # "approve" (our card) | "gateway" (platform card)
    approval: Approval | None = None
    loop: dict | None = None
    preamble: str = ""

    @property
    def current(self) -> Write:
        return self.writes[self.index]

    def to_metadata(self) -> dict:
        return {"writes": [w.to_dict() for w in self.writes], "index": self.index,
                "results": [list(r) for r in self.results], "notes": list(self.notes), "stage": self.stage,
                "approval": self.approval.to_dict() if self.approval else None, "loop": self.loop,
                "preamble": self.preamble}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Walk(writes=tuple(Write.from_dict(d) for d in data.get("writes") or ()),
                    index=int(data.get("index") or 0),
                    results=tuple((str(a), str(b)) for a, b in data.get("results") or ()),
                    notes=tuple(str(n) for n in data.get("notes") or ()), stage=str(data.get("stage") or "approve"),
                    approval=Approval.from_dict(data.get("approval")), loop=data.get("loop") or None,
                    preamble=str(data.get("preamble") or ""))


@dataclass(frozen=True)
class Outcome:
    kind: str                    # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None
    hitl: dict | None = None
    outcomes: dict | None = None
    envelope: dict | None = None


async def start(deps: TurnDeps, writes: Sequence[Write], preamble: str = "") -> Outcome:
    return _ask(Walk(writes=tuple(writes), preamble=preamble))


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping):
        return Outcome("complete", NOT_WAITING)
    walk = Walk.from_metadata(stored)
    if walk.index >= len(walk.writes):
        return Outcome("complete", NOT_WAITING)
    if walk.stage == "gateway":
        if walk.approval is None or not walk.loop:  # fail closed: never guess what was approved
            return _advance(walk, "not_written", f"{walk.current.label}: not written (the pause state was incomplete).")
        result = await resume_send(deps, walk.current, walk.approval, walk.loop, answer)
        return _after_send(walk, walk.approval, result)
    decision, edited = interpret_answer(answer)
    if decision == "skip":
        return _advance(walk, "skipped", f"{walk.current.label}: skipped, nothing written.")
    if decision == "edit":
        note = prompts.NUMBERS_FIXED if is_number_edit(edited) else prompts.NO_TEXT_EDITS
        return _ask(replace(walk, preamble=note))
    approval = approval_for(walk.current)
    log.info("write %s approved", walk.current.key)
    return _after_send(walk, approval, await send_one(deps, walk.current, approval))


def _ask(walk: Walk) -> Outcome:
    question, hitl = approval_question(walk.current)
    lead = walk.preamble or (walk.notes[-1] if walk.notes else "")
    progress = f"Write {walk.index + 1} of {len(walk.writes)}." if len(walk.writes) > 1 else ""
    text = "\n\n".join(part for part in (lead, progress, question) if part)
    return Outcome("input_required", text, _metadata(replace(walk, preamble="")), hitl, dict(walk.results) or None)


def _after_send(walk: Walk, approval: Approval, result: SendResult) -> Outcome:
    write = walk.current
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval, loop=result.loop, preamble="")
        text = "The platform needs your approval before this is written:\n\n" + describe(write)
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id},
                       dict(walk.results) or None)
    status = "written" if result.status == "sent" else result.status.replace("not_sent", "not_written")
    return _advance(walk, status, _note(write, result))


def _note(write: Write, result: SendResult) -> str:
    if result.status == "sent":
        return f"{write.label}: appended."
    if result.status == "denied":
        return f"{write.label}: the write was denied, so nothing was written and I won't retry it."
    reason = ("Writing is blocked for this agent." if result.status == "blocked"
              else f"Not written: {result.reason or 'unknown reason'}.")
    return f"{reason}\n\n{copyable(write)}"


def copyable(write: Write) -> str:
    if write.kind == "doc":
        return f"Here is the paragraph to paste into {write.target_name}:\n\n{write.text}"
    header = render.csv_line(write.header) if write.header else ""
    lines = "\n".join(p for p in (header, render.csv_line(write.values)) if p)
    return f"Here is the row to paste into {write.target_name} (CSV):\n\n```\n{lines}\n```"


def _advance(walk: Walk, status: str, note: str) -> Outcome:
    moved = replace(walk, index=walk.index + 1, results=(*walk.results, (walk.current.key, status)),
                    notes=(*walk.notes, note), stage="approve", approval=None, loop=None, preamble="")
    if moved.index < len(moved.writes):
        return _ask(moved)
    return Outcome("complete", "\n\n".join(moved.notes), _metadata(moved), None, dict(moved.results))


def _metadata(walk: Walk) -> dict:
    return {METADATA_KEY: walk.to_metadata()}
