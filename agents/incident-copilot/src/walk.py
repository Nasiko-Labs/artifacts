"""Pauses and their resumes, with all state on the A2A task between them.

Investigation pauses (which incident, when it started) resume by re-running the turn with what was
asked. The post walk:

    start → [channel? free text] → [input-required: Post (and tag) / Skip / typed note] → post_one
          ─┬─ posted / not posted / blocked → done
           └─ gateway ask → [auth-required] → authorized: re-run the same call; anything else: denied
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import card_render
import incident_step
import posting
import prompts
import records
from deps import TurnDeps
from discovery import discover
from posting import PostPlan
from send_guard import Approval
from sender import PostResult, approval_for, post_one, resume_post
from windows import iso, parse_start

log = logging.getLogger("incident_copilot.walk")

METADATA_KEY = "incident_copilot"
_CHANNEL_IN_ANSWER = re.compile(r"#[a-z0-9][a-z0-9_-]{0,79}", re.IGNORECASE)


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata: the pause, to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    outcomes: dict | None = None  # card hash → posted / skipped / denied / blocked / not_posted


@dataclass(frozen=True)
class Rerun:
    """Re-run the turn: the original request plus what the user answered, and anything already settled."""

    query: str
    preset: dict


def pause_outcome(pause: Mapping[str, Any]) -> Outcome:
    stored = {k: v for k, v in pause.items() if k not in ("text", "question")}
    return Outcome("input_required", str(pause["text"]), {METADATA_KEY: stored}, pause.get("question"))


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome | Rerun:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping):
        return Outcome("complete", prompts.NOT_WAITING)
    kind = stored.get("kind")
    query = str(stored.get("query") or "")
    if kind == "ask_target":
        return Rerun(f"{query}\n{answer}", {})
    if kind == "ask_start":
        start = parse_start(answer, deps.now)
        if start is None:
            return Outcome("complete", "I couldn't read a start time from that. " + prompts.ASK_START)
        return Rerun(query, {"start": iso(start), "alerting": stored.get("alerting") or {}})
    if kind == "pick_incident":
        chosen = incident_step.chosen(dict(stored), answer)
        return Rerun(query, {"incident": records.to_dict(chosen)}) if chosen else Rerun(f"{query}\n{answer}", {})
    if kind == "ask_channel":
        hint = _CHANNEL_IN_ANSWER.search(answer or "")
        request = {**dict(stored.get("request") or {}), "channel_hint": hint.group(0) if hint else answer.strip()}
        return await start_post(deps, request, asked=True)
    if kind == "approve":
        return await _approved(deps, PostPlan.from_dict(stored["plan"]), answer)
    if kind == "gateway":
        plan, approval = PostPlan.from_dict(stored["plan"]), Approval.from_dict(stored.get("approval"))
        if approval is None or not stored.get("loop"):
            return _done(plan, "not_posted", "Not posted: the pause state was incomplete.")
        return _after(plan, approval, await resume_post(deps, plan, approval, stored["loop"], answer))
    return Outcome("complete", prompts.NOT_WAITING)


async def start_post(deps: TurnDeps, request: Mapping[str, Any], asked: bool = False) -> Outcome:
    """`request`: card, card_hash, chat_ref, channel_hint, tag, suspect, posted (the hash already posted)."""
    card, card_hash = str(request.get("card") or ""), str(request.get("card_hash") or "")
    if not card:
        return Outcome("complete", prompts.NO_CARD)
    if request.get("posted") == card_hash:
        return Outcome("complete", "This card was already posted. Say \"check again\" for a fresh card to post.")
    catalog, failure = await discover(deps, "post")
    if catalog is None:
        return Outcome("complete", card_render.copyable(card, failure))
    tool = posting.choose_post_tool(catalog.write_tools())
    if tool is None:
        return Outcome("complete", card_render.copyable(card, "No chat posting tool is available to this agent."))
    chat_ref = str(request.get("chat_ref") or "")
    hint = chat_ref or str(request.get("channel_hint") or "")
    if not hint:
        return _ask_channel(request, "Which chat channel should I post the card to? (for example #inc-123)")
    channel_id, channel_name = await posting.resolve_channel(deps, catalog, hint, from_alerting=bool(chat_ref))
    if not channel_id:
        if asked or chat_ref:
            return Outcome("complete", card_render.copyable(card, f"I couldn't find the channel {hint} exactly."))
        return _ask_channel(request, f"I couldn't find the channel {hint} exactly. Which channel should I post to?")
    plan = PostPlan(card=card, card_hash=card_hash, channel_id=channel_id, channel_name=channel_name, tool=tool.name)
    if request.get("tag"):
        plan = await posting.with_tag(deps, catalog, plan, request.get("suspect"))
    text, hitl = posting.question(plan)
    return Outcome("input_required", text, {METADATA_KEY: {"kind": "approve", "plan": plan.to_dict()}}, hitl)


def _ask_channel(request: Mapping[str, Any], text: str) -> Outcome:
    return Outcome("input_required", text, {METADATA_KEY: {"kind": "ask_channel", "request": dict(request)}}, None)


async def _approved(deps: TurnDeps, plan: PostPlan, answer: str) -> Outcome:
    decision, tag, note = posting.interpret(answer, plan)
    if decision == "skip":
        return _done(plan, "skipped", "Skipped: nothing was posted.")
    approval = approval_for(plan, posting.body(plan, note, tag))
    log.info("card approved for %s (tag=%s, note=%s)", plan.channel_id, tag, bool(note))
    return _after(plan, approval, await post_one(deps, plan, approval))


def _after(plan: PostPlan, approval: Approval, result: PostResult) -> Outcome:
    if result.status == "paused":
        stored = {"kind": "gateway", "plan": plan.to_dict(), "approval": approval.to_dict(), "loop": result.loop}
        text = f"The platform needs your approval before the card is posted to {plan.channel_name}."
        return Outcome("auth_required", text, {METADATA_KEY: stored}, {"hitl_request_id": result.hitl_request_id})
    if result.status == "posted":
        return _done(plan, "posted", f"Posted the card to {plan.channel_name}.")
    if result.status == "denied":
        return _done(plan, "denied", "The post was denied, so nothing was posted and I won't retry it.")
    reason = ("Posting is blocked for this agent." if result.status == "blocked"
              else f"Not posted: {result.reason or 'unknown reason'}.")
    return _done(plan, result.status, card_render.copyable(approval.draft, reason))


def _done(plan: PostPlan, status: str, text: str) -> Outcome:
    return Outcome("complete", text, None, None, {plan.card_hash: status})
