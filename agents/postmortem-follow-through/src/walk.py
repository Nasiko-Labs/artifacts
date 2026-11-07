"""The approve → write walk: one action (a nudge to one owner, or one reopen) per pause, its state on the
A2A task between pauses.

    [select: pick items (nudge all …)] → prepare nudges ─┐
    start ───────────────────────────────────────────────┴→ [input-required: approve / Skip / edited text]
        → send ─┬─ done / failed / blocked → next action
                └─ gateway ask → [auth-required] → authorized: re-run the same call; else denied, never retried
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import nudge
import table_render
from actions import Action, approval_for, approval_question, describe, interpret_answer
from assess import Row
from deps import TurnDeps
from intake import numbers
from nasiko_hitl import MAX_OPTIONS, question_metadata
from send_guard import Approval
from sender import SendResult, resume_send, send

log = logging.getLogger("postmortem.walk")

METADATA_KEY = "postmortem"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
_LABEL_MAX = 150


@dataclass(frozen=True)
class Walk:
    actions: tuple[Action, ...] = ()
    index: int = 0
    results: tuple[tuple[str, str], ...] = ()   # (outcome key "nudge:OPS-1", status)
    notes: tuple[str, ...] = ()
    stage: str = "approve"                       # "select" | "approve" | "gateway"
    approval: Approval | None = None
    loop: dict | None = None
    guard: dict | None = None
    candidates: tuple[dict, ...] = ()            # rows offered on the select pause
    prior: dict = field(default_factory=dict)    # the conversation's outcomes when the walk began

    @property
    def current(self) -> Action:
        return self.actions[self.index]

    def to_metadata(self) -> dict:
        return {"actions": [a.to_dict() for a in self.actions], "index": self.index,
                "results": [list(r) for r in self.results], "notes": list(self.notes), "stage": self.stage,
                "approval": self.approval.to_dict() if self.approval else None, "loop": self.loop,
                "guard": self.guard, "candidates": list(self.candidates), "prior": dict(self.prior)}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Walk(actions=tuple(Action.from_dict(a) for a in data.get("actions") or ()),
                    index=int(data.get("index") or 0),
                    results=tuple((str(a), str(b)) for a, b in data.get("results") or ()),
                    notes=tuple(str(n) for n in data.get("notes") or ()), stage=str(data.get("stage") or "approve"),
                    approval=Approval.from_dict(data.get("approval")), loop=data.get("loop") or None,
                    guard=data.get("guard") or None, candidates=tuple(data.get("candidates") or ()),
                    prior={str(k): str(v) for k, v in (data.get("prior") or {}).items()})


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata: the walk, to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    outcomes: dict | None = None  # "nudge:KEY" / "reopen:KEY" → result so far, for the conversation


def start(actions: Sequence[Action], preamble: str = "") -> Outcome:
    return _ask(Walk(actions=tuple(actions), notes=(preamble,) if preamble else ()))


def start_select(rows: Sequence[Row], outcomes: Mapping[str, str], preamble: str = "") -> Outcome:
    """A multi-select of the rows to nudge (at most 20 options)."""
    shown = list(rows)[:MAX_OPTIONS]
    options = [(_option_label(r), f"{r.owner}, {r.state}") for r in shown]
    hitl = question_metadata("Which items should I nudge their owners about?", options, multi_select=True,
                             allow_custom_input=True)
    walk = Walk(stage="select", candidates=tuple(r.__dict__ for r in shown), prior=dict(outcomes))
    lines = [preamble] if preamble else []
    lines += ["Pick the items to nudge (one message per owner, each shown before it is sent):",
              *(f"- {label}" for label, _ in options)]
    return Outcome("input_required", "\n".join(lines), _metadata(walk), hitl)


def _option_label(row: Row) -> str:
    label = f"#{row.n} {row.key} {row.text}"
    encoded = label.encode("utf-8")
    return label if len(encoded) <= _LABEL_MAX else encoded[:_LABEL_MAX - 4].decode("utf-8", "ignore") + "…"


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping):
        return Outcome("complete", NOT_WAITING)
    walk = Walk.from_metadata(stored)
    if walk.stage == "select":
        return await _selected(deps, walk, answer)
    if walk.index >= len(walk.actions):
        return Outcome("complete", NOT_WAITING)
    if walk.stage == "gateway":
        if walk.approval is None or not walk.loop:  # fail closed: never guess what was approved
            return _advance(walk, "failed", f"{walk.current.who}: not done (the pause state was incomplete).")
        result = await resume_send(deps, walk.current, walk.approval, walk.loop, walk.guard or {}, answer)
        return _after_send(walk, walk.approval, result)
    decision, edited = interpret_answer(answer)
    if decision == "skip":
        return _advance(walk, "skipped", f"{walk.current.who}: skipped, nothing changed.")
    approval = approval_for(walk.current, edited or None)
    return _after_send(walk, approval, await send(deps, walk.current, approval))


async def _selected(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    picked = set(numbers(answer))
    rows = [Row(**{**r, "flags": tuple(r.get("flags") or ())}) for r in walk.candidates if str(r.get("n")) in picked]
    if not rows:
        return Outcome("complete", "Nothing was selected, so no nudge was sent.")
    prepared = await nudge.prepare(deps, rows, walk.prior)
    if not prepared.actions:
        return Outcome("complete", "\n\n".join(prepared.notes) or "There was nothing to nudge.")
    return _ask(Walk(actions=prepared.actions, notes=prepared.notes, prior=walk.prior))


def _ask(walk: Walk) -> Outcome:
    question, hitl = approval_question(walk.current)
    progress = f"Step {walk.index + 1} of {len(walk.actions)}." if len(walk.actions) > 1 else ""
    text = "\n\n".join(p for p in (*walk.notes[-3:], progress, question) if p)
    return Outcome("input_required", text, _metadata(walk), hitl, _outcomes(walk) or None)


def _after_send(walk: Walk, approval: Approval, result: SendResult) -> Outcome:
    action = walk.current
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval, loop=result.loop, guard=result.guard)
        text = "The platform needs your approval before this is written:\n\n" + describe(action)
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id},
                       _outcomes(walk) or None)
    return _advance(walk, result.status, _note(action, approval, result))


def _note(action: Action, approval: Approval, result: SendResult) -> str:
    text = next((w.body for w in approval.writes if w.body), action.text())
    if result.status == "nudged":
        return f"Nudged {action.who} about {', '.join(action.row_keys)}."
    if result.status == "reopened":
        if result.done.get("comment") == "ok":
            return f"Reopened {action.ref} and added the comment."
        return table_render.render_copyable(f"Comment for {action.ref}:", text,
                                            f"Reopened {action.ref}, but the comment was not added.")
    if result.status == "denied":
        return f"{action.who}: denied, so nothing was changed and I won't retry it."
    reason = ("Writing is blocked for this agent." if result.status == "blocked"
              else f"Not done: {result.reason or 'unknown reason'}.")
    return table_render.render_copyable(f"{action.who}:", text, reason)


def _advance(walk: Walk, status: str, note: str) -> Outcome:
    keys = tuple((f"{walk.current.kind}:{k}", status) for k in walk.current.row_keys)
    moved = replace(walk, index=walk.index + 1, results=(*walk.results, *keys), notes=(*walk.notes, note),
                    stage="approve", approval=None, loop=None, guard=None)
    if moved.index < len(moved.actions):
        return _ask(moved)
    return Outcome("complete", "\n\n".join(moved.notes), _metadata(moved), None, _outcomes(moved))


def _outcomes(walk: Walk) -> dict:
    return dict(walk.results)


def _metadata(walk: Walk) -> dict:
    return {METADATA_KEY: walk.to_metadata()}
