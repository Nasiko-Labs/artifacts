"""The approve → send walk: one ticket per pause, its state on the A2A task between pauses (adapted from
promise-tracker).

    start → [input-required: Send and solve / Send, keep open / Skip / edited text] → send_one
              ├─ posted / not posted / blocked → next ticket
              └─ gateway ask → [auth-required] → authorized: re-run the same call; anything else: denied

An edited reply that breaks a reply rule is shown once more with a warning line before it is posted."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

import draft_check
import render
from approval import Draft, approval_for, approval_question, describe, interpret_answer
from deps import TurnDeps
from draft_check import Facts
from pick import PHASE_KEY
from send_guard import Approval, Consumed
from sender import SendResult, resume_send, send_one

log = logging.getLogger("bug_notifier.walk")

NOT_WAITING = "This task isn't waiting on anything. Send a new request."


@dataclass(frozen=True)
class Walk:
    drafts: tuple[Draft, ...]
    facts: Facts
    index: int = 0
    results: tuple[tuple[str, str], ...] = ()   # (ticket id, sent/solved/skipped/denied/blocked/not_sent)
    notes: tuple[str, ...] = ()
    stage: str = "approve"                       # "approve" (our card) | "gateway" (the platform's card)
    approval: Approval | None = None
    loop: dict | None = None
    done: tuple[Consumed, Consumed] = (Consumed(), Consumed())   # (consumed, succeeded) at a gateway pause
    preamble: str = ""

    @property
    def current(self) -> Draft:
        return self.drafts[self.index]

    def to_metadata(self) -> dict:
        return {"phase": "walk", "drafts": [d.to_dict() for d in self.drafts], "facts": asdict(self.facts),
                "index": self.index, "results": [list(r) for r in self.results], "notes": list(self.notes),
                "stage": self.stage, "approval": self.approval.to_dict() if self.approval else None,
                "loop": self.loop, "done": [asdict(c) for c in self.done], "preamble": self.preamble}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        done = [Consumed(**{k: bool(v) for k, v in dict(c).items() if k in ("reply", "status")})
                for c in data.get("done") or ()]
        return Walk(drafts=tuple(Draft.from_dict(d) for d in data.get("drafts") or ()),
                    facts=Facts(**{k: str(v) for k, v in dict(data.get("facts") or {}).items()}),
                    index=int(data.get("index") or 0),
                    results=tuple((str(a), str(b)) for a, b in data.get("results") or ()),
                    notes=tuple(str(n) for n in data.get("notes") or ()), stage=str(data.get("stage") or "approve"),
                    approval=Approval.from_dict(data.get("approval")), loop=data.get("loop") or None,
                    done=(done[0], done[1]) if len(done) == 2 else (Consumed(), Consumed()),
                    preamble=str(data.get("preamble") or ""))


@dataclass(frozen=True)
class Outcome:
    kind: str                    # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    outcomes: dict | None = None  # ticket id → result so far, for the conversation's list


def start(drafts: Sequence[Draft], facts: Facts, preamble: str = "") -> Outcome:
    return _ask(Walk(drafts=tuple(drafts), facts=facts, preamble=preamble))


async def resume(deps: TurnDeps, stored: Mapping[str, Any], answer: str) -> Outcome:
    walk = Walk.from_metadata(stored)
    if walk.index >= len(walk.drafts):
        return Outcome("complete", NOT_WAITING)
    if walk.stage == "gateway":
        if walk.approval is None or not walk.loop:  # fail closed: never guess what was approved
            return _advance(walk, "not_sent", f"#{walk.current.ticket_id}: not posted (the pause state was incomplete).")
        return _after_send(walk, walk.approval, await resume_send(deps, walk.approval, walk.done, walk.loop, answer))
    decision, edited = interpret_answer(answer)
    draft = walk.current
    if decision == "skip":
        return _advance(walk, "skipped", f"#{draft.ticket_id}: skipped, nothing posted.")
    if decision == "edit":
        problems = draft_check.problems(edited, walk.facts, deps.config.max_reply_chars)
        if problems and not (draft.warnings and edited == draft.body):
            log.info("edited reply for #%s breaks a reply rule; asking once more", draft.ticket_id)
            changed = replace(draft, body=edited, warnings=problems)
            return _ask(replace(walk, drafts=(*walk.drafts[:walk.index], changed, *walk.drafts[walk.index + 1:])))
    approval = approval_for(draft, "keep" if decision == "edit" else decision, edited or None)
    log.info("reply for #%s approved (%s)", draft.ticket_id, decision)
    return _after_send(walk, approval, await send_one(deps, draft, approval))


def _ask(walk: Walk) -> Outcome:
    question, hitl = approval_question(walk.current)
    lead = walk.notes[-1] if walk.notes else walk.preamble
    progress = f"Reply {walk.index + 1} of {len(walk.drafts)}." if len(walk.drafts) > 1 else ""
    text = "\n\n".join(part for part in (lead, progress, question) if part)
    return Outcome("input_required", text, _metadata(walk), hitl, dict(walk.results) or None)


def _after_send(walk: Walk, approval: Approval, result: SendResult) -> Outcome:
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval, loop=result.loop,
                         done=(result.consumed, result.succeeded))
        text = "The platform needs your approval before this is posted:\n\n" + describe(walk.current, approval.body)
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id},
                       dict(walk.results) or None)
    return _advance(walk, result.status, _note(walk.current, approval, result))


def _note(draft: Draft, approval: Approval, result: SendResult) -> str:
    extra = f" ({result.reason})" if result.reason else ""
    if result.status == "solved":
        return f"#{draft.ticket_id}: reply posted for {draft.customer} and the ticket set to solved."
    if result.status == "sent":
        return f"#{draft.ticket_id}: reply posted for {draft.customer}{extra}."
    if result.status == "denied":
        return f"#{draft.ticket_id}: posting was denied, so nothing was posted and I won't retry it."
    reason = ("Posting is blocked for this agent." if result.status == "blocked"
              else f"Not posted: {result.reason or 'unknown reason'}.")
    return render.copyable(draft.ticket_id, draft.customer, approval.body, reason)


def _advance(walk: Walk, status: str, note: str) -> Outcome:
    moved = replace(walk, index=walk.index + 1, results=(*walk.results, (walk.current.ticket_id, status)),
                    notes=(*walk.notes, note), stage="approve", approval=None, loop=None,
                    done=(Consumed(), Consumed()))
    if moved.index < len(moved.drafts):
        return _ask(moved)
    return Outcome("complete", "\n\n".join(moved.notes), _metadata(moved), None, dict(moved.results))


def _metadata(walk: Walk) -> dict:
    return {PHASE_KEY: walk.to_metadata()}
