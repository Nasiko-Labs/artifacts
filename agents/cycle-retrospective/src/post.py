"""The pauses: "Which team?" (single-select) and the post card for one retro to one channel, and resuming
them. The post card offers Post / Skip; typed text may only be `drop prompts` or `drop spilled` (it never
reaches a model). Post → one guarded write; a gateway `ask` → auth-required; `block` → copyable text."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import render
import replies
from deps import TurnDeps
from nasiko_hitl import MAX_QUESTION_HEADER_LEN, question_metadata
from records import Team
from retro import Retro
from send_guard import Approval, strip_mentions
from sender import SendResult, resume_send, send_one

PHASE_KEY = "cycle_retro"
POST, SKIP = "Post", "Skip"
DROPS = {"drop prompts": "prompts", "drop spilled": "spilled"}
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
_POST_WORDS = frozenset({"post", "post it", "yes", "y", "ok", "okay", "approve", "approved", "send", "go"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't post", "dont post", "stop", "deny", "denied", ""})


@dataclass(frozen=True)
class Pause:
    text: str
    hitl: dict
    metadata: dict


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    data: dict | None = None      # {"envelope": ...}


def clip(text: str, max_bytes: int) -> str:
    data = text.encode("utf-8")
    return text if len(data) <= max_bytes else data[:max_bytes - 3].decode("utf-8", errors="ignore") + "..."


def team_pause(teams: Sequence[Team], query: str, notes: Sequence[str] = ()) -> Pause:
    hitl = question_metadata(replies.ASK_TEAM, [(clip(t.name, 200), f"Team id {t.id}"[:200]) for t in teams],
                             multi_select=False, allow_custom_input=True)
    metadata = {PHASE_KEY: {"phase": "team", "teams": [t.name for t in teams], "query": query}}
    return Pause("\n\n".join([*notes, f"I found {len(teams)} teams. {replies.ASK_TEAM}"]), hitl, metadata)


def chosen_team(answer: str, names: Sequence[str]) -> str | None:
    text = (answer or "").strip().casefold()
    return next((n for n in names if n.casefold() == text), None) or next(
        (n for n in names if n.casefold() in text), None)


def post_body(retro: Retro, drop: Sequence[str], limit: int) -> tuple[str, bool]:
    body, shortened = render.post_body(retro, drop, limit)
    return strip_mentions(body), shortened


def post_pause(retro: Retro, channel: str, tools: Sequence[str], drop: Sequence[str], limit: int,
               lead: str = "") -> Pause:
    body, shortened = post_body(retro, drop, limit)
    header = clip(f"Post this retro to #{channel}?", MAX_QUESTION_HEADER_LEN)
    hitl = question_metadata(header, [(POST, f"Post exactly the text shown to #{channel}, once."),
                                      (SKIP, "Post nothing; the retro stays here to copy.")],
                             multi_select=False, allow_custom_input=True)
    shown = ("This is what will be posted (shortened by code to fit):" if shortened
             else "This is what will be posted:") + f"\n\n{body}"
    hint = f"Pick {POST} or {SKIP}, or type \"drop prompts\" or \"drop spilled\" to post without that section."
    metadata = {PHASE_KEY: {"phase": "post", "retro": retro.to_state(), "channel": channel, "tools": list(tools),
                            "drop": list(drop)}}
    return Pause("\n\n".join(p for p in (lead, shown, header + " " + hint) if p), hitl, metadata)


async def resume_post(deps: TurnDeps, phase: Mapping[str, Any], answer: str) -> Outcome:
    retro = Retro.from_state(phase["retro"])
    channel, tools = str(phase.get("channel") or ""), tuple(str(t) for t in phase.get("tools") or ())
    drop = [str(d) for d in phase.get("drop") or ()]
    body, _ = post_body(retro, drop, deps.config.max_post_chars)
    approval = Approval(channel=channel, body=body, tools=tools)
    if phase.get("stage") == "gateway":
        stored = Approval.from_dict(phase.get("approval"))
        if stored is None or not phase.get("loop"):
            return Outcome("complete", render.copyable(body, "Not posted: the pause state was incomplete."))
        result = await resume_send(deps, stored, bool(phase.get("consumed")), str(phase.get("seen_text") or ""),
                                   phase["loop"], answer)
        return _after_send(phase, stored, result)
    word = (answer or "").strip().casefold().rstrip(".!")
    if word in _SKIP_WORDS:
        return Outcome("complete", replies.NOT_POSTED)
    if word in DROPS:
        pause = post_pause(retro, channel, tools, [*drop, DROPS[word]], deps.config.max_post_chars)
        return Outcome("input_required", pause.text, pause.metadata, pause.hitl)
    if word not in _POST_WORDS:
        pause = post_pause(retro, channel, tools, drop, deps.config.max_post_chars, lead=replies.EDIT_HINT)
        return Outcome("input_required", pause.text, pause.metadata, pause.hitl)
    return _after_send(phase, approval, await send_one(deps, approval))


def _after_send(phase: Mapping[str, Any], approval: Approval, result: SendResult) -> Outcome:
    if result.status == "paused":
        stored = {**dict(phase), "stage": "gateway", "approval": approval.to_dict(), "loop": result.loop,
                  "consumed": result.consumed, "seen_text": result.seen_text}
        return Outcome("auth_required", f"The platform needs your approval before the retro is posted to "
                                        f"#{approval.channel}.", {PHASE_KEY: stored},
                       {"hitl_request_id": result.hitl_request_id})
    if result.status == "sent":
        return Outcome("complete", f"Posted the retro to #{approval.channel}.")
    if result.status == "denied":
        return Outcome("complete", "Posting was denied, so nothing was posted and I won't retry it.")
    reason = ("Posting is blocked for this agent." if result.status == "blocked"
              else f"Not posted: {result.reason or 'unknown reason'}.")
    return Outcome("complete", render.copyable(approval.body, reason))
