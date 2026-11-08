"""The approve -> post walk for ONE message, its state on the A2A task between pauses.

    card "Post this to #channel?" -+- Post ------------------> send_one -+- posted / not posted / blocked -> done
                                   +- Skip -> done (nothing posted)       +- gateway ask -> [auth-required]
                                   +- typed text = the edited post -+- numbers re-checked: ok -> send_one
                                                                    +- a number not in the check -> card again

One approval = one write: the send guard binds tool + channel id + exact body, and a completed call consumes the
approval. A `block` stance or a missing post tool turns into copyable text. (Pattern copied from the hardened kit;
owned by this agent.)
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

import post as posts
import prompts
import render
from deps import TurnDeps
from nasiko_hitl import question_metadata
from send_guard import Approval
from sender import Post, SendResult, approval_for, resume_send, send_one

log = logging.getLogger("revenue_anomaly.walk")

PHASE_KEY = "revenue_anomaly_phase"
POST = "Post"
SKIP = "Skip"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
_HEADER_TARGET_MAX = 150  # bytes; keeps the header inside the platform's 200-byte limit
_POST_WORDS = frozenset({"post", "post it", "send", "send it", "yes", "y", "ok", "okay", "approve", "approved"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't post", "dont post", "stop", "deny", "denied",
                         "reject", "decline"})

Decision = Literal["post", "skip", "edit"]
OutcomeKind = Literal["complete", "input_required", "auth_required"]


@dataclass(frozen=True)
class Outcome:
    kind: OutcomeKind
    text: str
    metadata: dict | None = None   # task metadata: the phase to resume from
    hitl: dict | None = None       # status-message metadata: the question, or the gateway's hitl_request_id
    data: dict | None = None       # the envelope, as an A2A data part (completed check turns only)
    posted: str = ""               # the channel label a post went to (recorded in the conversation)


@dataclass(frozen=True)
class Walk:
    post: Post
    allowed: tuple[str, ...]       # the numbers the post may carry (from the check it was drafted from)
    stage: str = "approve"         # "approve" (our card) | "gateway" (the platform's card)
    approval: Approval | None = None
    loop: dict | None = None

    def to_metadata(self) -> dict:
        return {PHASE_KEY: {"phase": "walk", "post": self.post.to_dict(), "allowed": list(self.allowed),
                            "stage": self.stage, "approval": self.approval.to_dict() if self.approval else None,
                            "loop": self.loop}}

    @staticmethod
    def from_phase(data: Mapping[str, Any]) -> Walk | None:
        post = Post.from_dict(data.get("post"))
        if post is None:
            return None
        return Walk(post=post, allowed=tuple(str(a) for a in data.get("allowed") or ()),
                    stage=str(data.get("stage") or "approve"), approval=Approval.from_dict(data.get("approval")),
                    loop=data.get("loop") or None)


def start(post: Post, allowed: Sequence[str], preamble: str = "") -> Outcome:
    return _ask(Walk(post=post, allowed=tuple(allowed)), preamble)


def interpret_answer(answer: str) -> tuple[Decision, str]:
    """Post / Skip / an edited text. Empty and unclear refusals are Skip: nothing goes out by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _SKIP_WORDS:
        return "skip", ""
    if word in _POST_WORDS:
        return "post", ""
    return "edit", text


async def resume(deps: TurnDeps, phase: Mapping[str, Any], answer: str) -> Outcome:
    walk = Walk.from_phase(phase)
    if walk is None:
        return Outcome("complete", NOT_WAITING)
    if walk.stage == "gateway":
        if walk.approval is None or not walk.loop:  # fail closed: never guess what was approved
            return Outcome("complete", "Not posted: the pause state was incomplete.")
        return _after_send(walk, walk.approval, await resume_send(deps, walk.post, walk.approval, walk.loop, answer))
    decision, edited = interpret_answer(answer)
    if decision == "skip":
        return Outcome("complete", "Skipped: nothing was posted.")
    if decision == "edit":
        problem = posts.edit_problem(edited, frozenset(walk.allowed))
        if problem is not None:  # edited numbers are re-checked like the model's
            return _ask(walk, f"I can't post your edit: {problem}. Post the text below, skip, or type a corrected "
                              "text.")
    approval = approval_for(walk.post, edited or None)
    log.info("post to %s approved (%s)", walk.post.target, decision)
    return _after_send(walk, approval, await send_one(deps, walk.post, approval))


def card(post: Post, body: str) -> str:
    return "\n".join([f"To: {post.target}", f"Via: {post.tool}", "",
                      *(f"> {x}" if x else ">" for x in body.split("\n"))])


def _ask(walk: Walk, lead: str = "") -> Outcome:
    target = walk.post.target
    name = target if len(target.encode("utf-8")) <= _HEADER_TARGET_MAX else "the chosen channel"
    hitl = question_metadata(f"Post this to {name}?", [
        (POST, "Post exactly this text, once."),
        (SKIP, "Don't post it."),
    ], multi_select=False, allow_custom_input=True)
    text = "\n\n".join(p for p in (lead, card(walk.post, walk.post.body),
                                   "Reply Post, Skip, or type your own text to post instead.") if p)
    return Outcome("input_required", text, walk.to_metadata(), hitl)


def _after_send(walk: Walk, approval: Approval, result: SendResult) -> Outcome:
    post = walk.post
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval, loop=result.loop)
        text = "The platform needs your approval before this is posted:\n\n" + card(post, approval.draft)
        return Outcome("auth_required", text, paused.to_metadata(), {"hitl_request_id": result.hitl_request_id})
    if result.status == "sent":
        return Outcome("complete", f"Posted to {post.target}.", posted=post.target)
    if result.status == "denied":
        return Outcome("complete", "The post was denied, so nothing was posted and I won't retry it.")
    if result.status == "blocked":
        return Outcome("complete", render.copyable(post.target, approval.draft, prompts.SEND_BLOCKED))
    return Outcome("complete", render.copyable(post.target, approval.draft,
                                               f"Not posted: {result.reason or 'unknown reason'}."))
