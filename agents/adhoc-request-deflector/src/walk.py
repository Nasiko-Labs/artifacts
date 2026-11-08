"""The approve → post walk: one reply per pause (one approval card per write), its state on the A2A task
between pauses.

    start → [input-required: Post / Skip / edited text] → post_one ─┬─ posted / not sent / blocked → next
                                                                     └─ gateway ask → [auth-required]
                                                                          → authorized: the same call again
                                                                          → anything else: denied, never retried

(Adapted from the MCP kit; owned here.)
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Literal

import render
from deps import TurnDeps
from nasiko_hitl import MAX_QUESTION_HEADER_LEN, question_metadata
from send_guard import Approval
from sender import SendResult, post_one, resume_post

log = logging.getLogger("deflector.walk")

METADATA_KEY = "deflector_walk"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
POST, SKIP = "Post", "Skip"
_POST_WORDS = frozenset({"post", "post it", "yes", "y", "ok", "okay", "approve", "approved", "send", "send it"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't post", "dont post", "stop", "deny", "denied", "reject"})

Decision = Literal["post", "skip", "edit"]


@dataclass(frozen=True)
class Draft:
    number: int          # the item's number in the list shown
    requester: str
    channel_id: str
    channel_label: str
    thread_ts: str
    request_link: str
    body: str
    tool: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Draft:
        values = {f.name: data.get(f.name) for f in fields(Draft)}
        return Draft(**{k: (int(v or 0) if k == "number" else str(v or "")) for k, v in values.items()})


@dataclass(frozen=True)
class Walk:
    drafts: tuple[Draft, ...]
    index: int = 0
    results: tuple[tuple[int, str], ...] = ()    # (item number, posted/skipped/denied/blocked/not_sent)
    notes: tuple[str, ...] = ()
    stage: str = "approve"                        # "approve" (our card) | "gateway" (platform card)
    approval: Approval | None = None
    arguments: dict | None = None
    preamble: str = ""

    @property
    def current(self) -> Draft:
        return self.drafts[self.index]

    def to_metadata(self) -> dict:
        return {"drafts": [d.to_dict() for d in self.drafts], "index": self.index,
                "results": [[n, s] for n, s in self.results], "notes": list(self.notes), "stage": self.stage,
                "approval": self.approval.to_dict() if self.approval else None, "arguments": self.arguments,
                "preamble": self.preamble}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Walk(drafts=tuple(Draft.from_dict(d) for d in data.get("drafts") or ()),
                    index=int(data.get("index") or 0),
                    results=tuple((int(n), str(s)) for n, s in data.get("results") or ()),
                    notes=tuple(str(n) for n in data.get("notes") or ()), stage=str(data.get("stage") or "approve"),
                    approval=Approval.from_dict(data.get("approval")),
                    arguments=dict(data["arguments"]) if isinstance(data.get("arguments"), Mapping) else None,
                    preamble=str(data.get("preamble") or ""))


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata: what to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    outcomes: dict | None = None  # item number (str) → result so far
    envelope: dict | None = None


def approval_for(draft: Draft, body: str | None = None) -> Approval:
    """The one write the human approved: this tool, this channel, this thread, this text."""
    return Approval(action="post", target=f"{draft.requester or 'the requester'}'s thread in {draft.channel_label}",
                    draft=body or draft.body, tool=draft.tool, channel=draft.channel_id, thread=draft.thread_ts)


def interpret_answer(answer: str) -> tuple[Decision, str]:
    """Post / Skip / an edited text. Empty and unclear refusals are Skip: nothing is posted by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _SKIP_WORDS:
        return "skip", ""
    if word in _POST_WORDS:
        return "post", ""
    return "edit", text


def question(draft: Draft) -> tuple[str, dict]:
    who = draft.requester or "the requester"
    header = f"Post this reply in {who}'s thread?"
    if len(header.encode("utf-8")) > MAX_QUESTION_HEADER_LEN:
        header = "Post this reply in the request's thread?"
    hitl = question_metadata(header, [(POST, "Post exactly this text in the thread."),
                                      (SKIP, "Don't post it; move on.")], multi_select=False, allow_custom_input=True)
    return f"{describe(draft, draft.body)}\n\nReply Post, Skip, or type the text to post instead.", hitl


def describe(draft: Draft, body: str) -> str:
    lines = [f"Reply for #{draft.number} ({draft.requester or 'unknown requester'}, {draft.channel_label})",
             f"Thread: {draft.request_link or draft.thread_ts}", f"Via: {draft.tool}", "", render.quote(body)]
    return "\n".join(lines)


async def start(deps: TurnDeps, drafts: Sequence[Draft], preamble: str = "", envelope: dict | None = None) -> Outcome:
    return replace(_ask(Walk(drafts=tuple(drafts), preamble=preamble)), envelope=envelope)


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping):
        return Outcome("complete", NOT_WAITING)
    walk = Walk.from_metadata(stored)
    if walk.index >= len(walk.drafts):
        return Outcome("complete", NOT_WAITING)
    if walk.stage == "gateway":
        if walk.approval is None or not walk.arguments:  # fail closed: never guess what was approved
            return _advance(walk, "not_sent", f"#{walk.current.number}: not posted (the pause state was incomplete).")
        result = await resume_post(deps, walk.approval, walk.arguments, answer)
        return _after_post(walk, walk.approval, result)
    decision, edited = interpret_answer(answer)
    if decision == "skip":
        return _advance(walk, "skipped", f"#{walk.current.number}: skipped, nothing posted.")
    approval = approval_for(walk.current, edited or None)
    log.info("reply #%s approved (%s)", walk.current.number, decision)
    return _after_post(walk, approval, await post_one(deps, approval))


def _ask(walk: Walk) -> Outcome:
    text, hitl = question(walk.current)
    lead = walk.notes[-1] if walk.notes else walk.preamble
    progress = f"Reply {walk.index + 1} of {len(walk.drafts)}." if len(walk.drafts) > 1 else ""
    body = "\n\n".join(part for part in (lead, progress, text) if part)
    return Outcome("input_required", body, _metadata(walk), hitl, _results(walk) or None)


def _after_post(walk: Walk, approval: Approval, result: SendResult) -> Outcome:
    draft = walk.current
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval, arguments=result.arguments)
        text = "The platform needs your approval before this is posted:\n\n" + describe(draft, approval.draft)
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id},
                       _results(walk) or None)
    return _advance(walk, result.status, _note(draft, approval, result))


def _note(draft: Draft, approval: Approval, result: SendResult) -> str:
    if result.status == "posted":
        return f"#{draft.number}: posted in {draft.requester or 'the requester'}'s thread."
    if result.status == "denied":
        return f"#{draft.number}: the post was denied, so nothing was posted and I won't retry it."
    reason = "Posting is blocked for this agent." if result.status == "blocked" else \
        f"Not posted: {result.reason or 'unknown reason'}."
    return render.copyable(draft.number, reason, approval.draft)


def _advance(walk: Walk, status: str, note: str) -> Outcome:
    moved = replace(walk, index=walk.index + 1, results=(*walk.results, (walk.current.number, status)),
                    notes=(*walk.notes, note), stage="approve", approval=None, arguments=None)
    if moved.index < len(moved.drafts):
        return _ask(moved)
    return Outcome("complete", "\n\n".join(moved.notes), _metadata(moved), None, _results(moved))


def _results(walk: Walk) -> dict:
    return {str(n): s for n, s in walk.results}


def _metadata(walk: Walk) -> dict:
    return {METADATA_KEY: walk.to_metadata()}
