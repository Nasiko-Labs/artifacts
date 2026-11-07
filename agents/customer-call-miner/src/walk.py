"""The write walk, its state on the A2A task between pauses:

    notes card (multi-select, ≤ 20 accounts) → each selected note written one at a time ─┬→ next card
        (a gateway `ask` on a note → auth-required; `authorized` re-runs it, else denied) ┘
    → digest card (Post / Skip / edited text), only when the user named a channel → post → done

A single-note card (Send / Skip / edited text) replaces the notes card when the user asks to edit one.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import approvals as ap
from deps import TurnDeps
from records import NoteDraft, dump_all, note_from
from send_guard import Approval
from sender import SendResult, resume_send, send_one

log = logging.getLogger("call_miner.walk")

METADATA_KEY = "call_miner"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
DIGEST_KEY = "digest"


@dataclass(frozen=True)
class Walk:
    notes: tuple[NoteDraft, ...] = ()
    batch: int = 0
    queue: tuple[str, ...] = ()                  # approved company ids still to write
    edits: tuple[tuple[str, str], ...] = ()      # company id → edited note text
    digest: ap.DigestPlan | None = None
    results: tuple[tuple[str, str], ...] = ()    # (company id or "digest", status)
    lines: tuple[str, ...] = ()
    stage: str = "notes"                         # notes | single | digest | note_gateway | digest_gateway
    approval: Approval | None = None
    loop: dict | None = None
    note_tool: str = ""
    assoc_tool: str = ""
    preamble: str = ""

    def to_metadata(self) -> dict:
        return {"notes": dump_all(self.notes), "batch": self.batch, "queue": list(self.queue),
                "edits": [list(e) for e in self.edits], "digest": self.digest.to_dict() if self.digest else None,
                "results": [list(r) for r in self.results], "lines": list(self.lines), "stage": self.stage,
                "approval": self.approval.to_dict() if self.approval else None, "loop": self.loop,
                "note_tool": self.note_tool, "assoc_tool": self.assoc_tool, "preamble": self.preamble}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Walk(notes=tuple(note_from(n) for n in data.get("notes") or ()), batch=int(data.get("batch") or 0),
                    queue=tuple(str(q) for q in data.get("queue") or ()),
                    edits=tuple((str(a), str(b)) for a, b in data.get("edits") or ()),
                    digest=ap.DigestPlan.from_dict(data.get("digest")),
                    results=tuple((str(a), str(b)) for a, b in data.get("results") or ()),
                    lines=tuple(str(x) for x in data.get("lines") or ()), stage=str(data.get("stage") or "notes"),
                    approval=Approval.from_dict(data.get("approval")), loop=data.get("loop") or None,
                    note_tool=str(data.get("note_tool") or ""), assoc_tool=str(data.get("assoc_tool") or ""),
                    preamble=str(data.get("preamble") or ""))

    def note(self, company_id: str) -> NoteDraft | None:
        return next((n for n in self.notes if n.company_id == company_id), None)


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata: the walk, to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    outcomes: dict | None = None  # company id / "digest" → result, for the conversation's state


async def start(deps: TurnDeps, walk: Walk) -> Outcome:
    if walk.notes and walk.stage == "single":
        return _ask(walk)
    if walk.notes:
        return _ask(replace(walk, stage="notes"))
    if walk.digest:
        return _ask(replace(walk, stage="digest"))
    return Outcome("complete", walk.preamble)


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping):
        return Outcome("complete", NOT_WAITING)
    walk = Walk.from_metadata(stored)
    if walk.stage in ("note_gateway", "digest_gateway"):
        return await _resume_gateway(deps, walk, answer)
    if walk.stage == "digest" and walk.digest:
        decision, edited = ap.interpret(answer)
        if decision == "skip":
            return _finish(_record(walk, DIGEST_KEY, "skipped", "Digest: skipped, nothing posted."))
        approval = ap.digest_approval(walk.digest, edited or None)
        return _after_digest(walk, approval, await send_one(deps, "digest", approval, _digest_request(walk.digest)))
    if walk.stage == "single" and walk.notes:
        decision, edited = ap.interpret(answer)
        note = walk.notes[0]
        if decision == "skip":
            return await _proceed(deps, _record(walk, note.company_id, "skipped", f"{note.name}: skipped, no note added."))
        edits = ((note.company_id, edited),) if edited else ()
        return await _proceed(deps, replace(walk, queue=(note.company_id,), edits=edits))
    batch = ap.batches(walk.notes)[walk.batch] if walk.batch < len(ap.batches(walk.notes)) else ()
    picked = ap.selected(answer, batch)
    walk = _skip_unpicked(walk, batch, picked)
    return await _proceed(deps, replace(walk, queue=tuple(n.company_id for n in picked)))


def _skip_unpicked(walk: Walk, batch: Sequence[NoteDraft], picked: Sequence[NoteDraft]) -> Walk:
    ids = {n.company_id for n in picked}
    for note in batch:
        if note.company_id not in ids:
            walk = replace(walk, results=(*walk.results, (note.company_id, "skipped")))
    skipped = len(batch) - len(ids)
    return replace(walk, lines=(*walk.lines, f"{skipped} note(s) not selected, not added.")) if skipped else walk


async def _proceed(deps: TurnDeps, walk: Walk) -> Outcome:
    """Write the queued notes one at a time, then the next card, then the digest card."""
    while walk.queue:
        note = walk.note(walk.queue[0])
        if note is None:
            walk = replace(walk, queue=walk.queue[1:])
            continue
        approval = ap.note_approval(note, walk.note_tool, walk.assoc_tool, dict(walk.edits).get(note.company_id))
        result = await send_one(deps, "notes", approval, _note_request(note))
        if result.status == "paused":
            return _gateway_pause(replace(walk, stage="note_gateway", approval=approval, loop=result.loop),
                                  result, f"Add the note to {note.name}")
        walk = _after_note(walk, note, approval, result)
    if walk.stage == "notes" and walk.batch + 1 < len(ap.batches(walk.notes)):
        return _ask(replace(walk, batch=walk.batch + 1))
    if walk.digest and DIGEST_KEY not in dict(walk.results):
        return _ask(replace(walk, stage="digest"))
    return _finish(walk)


async def _resume_gateway(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    if walk.approval is None or not walk.loop:  # fail closed: never guess what was approved
        return _finish(replace(walk, lines=(*walk.lines, "Not written: the pause state was incomplete.")))
    if walk.stage == "digest_gateway":
        return _after_digest(walk, walk.approval, await resume_send(deps, "digest", walk.approval, walk.loop, answer))
    note = walk.note(walk.queue[0]) if walk.queue else None
    result = await resume_send(deps, "notes", walk.approval, walk.loop, answer)
    if note is None:
        return _finish(walk)
    if result.status == "paused":
        return _gateway_pause(replace(walk, loop=result.loop), result, f"Add the note to {note.name}")
    walk = _after_note(replace(walk, stage="notes", approval=None, loop=None), note, walk.approval, result)
    return await _proceed(deps, walk)


def _after_note(walk: Walk, note: NoteDraft, approval: Approval, result: SendResult) -> Walk:
    walk = replace(walk, queue=walk.queue[1:])
    return _record(walk, note.company_id, result.status, _line(note.name, "note", approval, result))


def _after_digest(walk: Walk, approval: Approval, result: SendResult) -> Outcome:
    if result.status == "paused":
        return _gateway_pause(replace(walk, stage="digest_gateway", approval=approval, loop=result.loop), result,
                              f"Post the digest to #{walk.digest.channel_name.lstrip('#')}")
    return _finish(_record(walk, DIGEST_KEY, result.status, _line(f"#{walk.digest.channel_name.lstrip('#')}",
                                                                  "digest", approval, result)))


def _line(target: str, what: str, approval: Approval, result: SendResult) -> str:
    if result.status == "sent":
        return f"{target}: {'note added' if what == 'note' else 'digest posted'}."
    if result.status == "denied":
        return f"{target}: the write was denied, so nothing was written and I won't retry it."
    reason = "Writing is blocked for this agent." if result.status == "blocked" \
        else f"Not written: {result.reason or 'unknown reason'}."
    return f"{target}: {reason} Here is the text to copy:\n\n{approval.draft}"


def _record(walk: Walk, key: str, status: str, line: str) -> Walk:
    return replace(walk, results=(*walk.results, (key, status)), lines=(*walk.lines, line))


def _gateway_pause(walk: Walk, result: SendResult, what: str) -> Outcome:
    text = f"The platform needs your approval before this is written: {what}."
    return Outcome("auth_required", text, _metadata(walk), {"hitl_request_id": result.hitl_request_id},
                   dict(walk.results) or None)


def _ask(walk: Walk) -> Outcome:
    if walk.stage == "digest":
        question, hitl = ap.digest_question(walk.digest)
    elif walk.stage == "single":
        question, hitl = ap.single_note_question(walk.notes[0])
    else:
        groups = ap.batches(walk.notes)
        more = sum(len(g) for g in groups[walk.batch + 1:])
        question, hitl = ap.notes_question(groups[walk.batch], more)
    lead = "\n".join(walk.lines) if walk.lines else walk.preamble
    text = "\n\n".join(part for part in (lead, question) if part)
    return Outcome("input_required", text, _metadata(walk), hitl, dict(walk.results) or None)


def _finish(walk: Walk) -> Outcome:
    text = "\n".join(walk.lines) or "Nothing was written."
    return Outcome("complete", text, _metadata(walk), None, dict(walk.results) or None)


def _metadata(walk: Walk) -> dict:
    return {METADATA_KEY: walk.to_metadata()}


def _note_request(note: NoteDraft) -> str:
    return (f"Add a note to the CRM company record with id {note.company_id} ({note.name}). The note text is the "
            "approved message below.")


def _digest_request(plan: ap.DigestPlan) -> str:
    return (f"Post a message to the channel with id {plan.channel_id} (#{plan.channel_name.lstrip('#')}), not in a "
            "thread. The message text is the approved message below.")
