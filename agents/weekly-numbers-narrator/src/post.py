"""Posting the narrative: find the post tool and the channel, the channel pick, the approval card, and the
approve -> post walk (one post per approval, its state on the A2A task between pauses).

    prepare -+- no post tool / dry run -> copyable text
             +- no channel named -> [input-required: pick a member channel] -> card
             +- channel -> card [input-required: Post / Skip / edited text]
                 -> Post -> send_one (guarded) -+- posted / not posted / blocked (copyable text)
                                                +- gateway ask -> [auth-required] -> authorized: same call
                 -> edited text -> the same card again with that text (the guard binds exactly it)
                 -> Skip -> nothing posted
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import prompts
import render
from deps import TurnDeps
from discovery import discover
from nasiko_hitl import question_metadata
from send_guard import Approval
from sender import SendResult, resume_send, send_one
from sheet import Reader, appears_in, load_json_object, reader_for

log = logging.getLogger("numbers_narrator.post")

WALK_KEY = "wnn.post"
CHANNEL_KEY = "wnn.channel_pick"
POST = "Post"
SKIP = "Skip"
CHANNELS_LOOP_CALLS = 3
MAX_CHANNELS = 10
MIN_EDIT_WORDS = 4
_HEADER_MAX = 200
_YES = frozenset({"post", "yes", "y", "ok", "okay", "approve", "approved", "go", "do it", "post it", "send", "send it",
                  "confirm"})
_NO = frozenset({"skip", "no", "n", "cancel", "stop", "deny", "denied", "reject", "rejected", "decline", "don't",
                 "dont"})
_POST_WORDS = ("post", "send", "message")
NOT_WAITING = "This task isn't waiting on anything. Send a new request."


@dataclass(frozen=True)
class Draft:
    tool: str
    channel_id: str
    channel_name: str
    text: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Draft:
        return Draft(*(str(data.get(k) or "") for k in ("tool", "channel_id", "channel_name", "text")))

    @property
    def label(self) -> str:
        return f"#{self.channel_name.lstrip('#')}"


@dataclass(frozen=True)
class Outcome:
    kind: str                    # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None
    hitl: dict | None = None
    remember: dict | None = None  # checkpointer update after a post decision
    envelope: dict | None = None


@dataclass(frozen=True)
class Plan:
    """What `prepare` found: a card to show, a channel question, or copyable text with a reason."""

    draft: Draft | None = None
    question: tuple[str, dict, dict] | None = None   # (text, hitl, task metadata)
    copy_reason: str = ""


# --- prepare ------------------------------------------------------------------------------------------------

async def prepare(deps: TurnDeps, text: str, channel: str | None, dry_run: bool) -> Plan:
    if dry_run:
        return Plan(copy_reason=prompts.DRY_RUN_NOTE)
    if not deps.traceparent:
        return Plan(copy_reason=prompts.NO_TRACE_CONTEXT)
    catalog, failure = await discover(deps, ("read", "send"))
    if failure is not None:
        return Plan(copy_reason=prompts.NO_CHAT_TOOL)
    tool = pick_post_tool(catalog.send_tools("chat"))
    if tool is None:
        return Plan(copy_reason=prompts.NO_CHAT_TOOL)
    reader = reader_for(deps, catalog)
    channels = await list_channels(deps, reader) if reader.tools_for("chat") else ()
    if channel:
        found = next((c for c in channels if _same(c[1], channel)), None)
        return Plan(Draft(tool, found[0] if found else channel, found[1] if found else channel, text))
    if not channels:
        return Plan(copy_reason=prompts.NO_CHANNEL)
    options = channels[:MAX_CHANNELS]
    hitl = question_metadata(prompts.CHANNEL_HEADER, [(f"#{name.lstrip('#')}", "") for _, name in options],
                             multi_select=False, allow_custom_input=True)
    names = {f"#{name.lstrip('#')}": cid for cid, name in options}
    metadata = {CHANNEL_KEY: {"tool": tool, "text": text, "channels": names}}
    return Plan(question=(prompts.CHANNEL_HEADER, hitl, metadata))


def pick_post_tool(tools) -> str | None:
    ranked = sorted(tools, key=lambda t: -sum(w in t.name.casefold() for w in _POST_WORDS))
    best = next((t for t in ranked if any(w in t.name.casefold() for w in _POST_WORDS)), None)
    return best.name if best is not None else None


async def list_channels(deps: TurnDeps, reader: Reader) -> tuple[tuple[str, str], ...]:
    """(id, name) of channels a tool returned this turn, verbatim."""
    result = await reader.loop(deps, "chat", ({"role": "system", "content": prompts.CHANNELS_SYSTEM},
                                              {"role": "user", "content": "List my channels."}), CHANNELS_LOOP_CALLS)
    data = load_json_object(result.text) if result.status == "completed" else None
    items = data.get("channels") if data else None
    if not isinstance(items, list):
        return ()
    seen = reader.fenced.seen_text
    found: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        cid, name = str(item.get("id") or "").strip(), str(item.get("name") or "").strip().lstrip("#")
        if appears_in(cid, seen) and appears_in(name, seen) and all(n != name for _, n in found):
            found.append((cid, name))
    return tuple(found)


def picked_channel(pick: Mapping[str, Any], answer: str) -> Draft | None:
    names = {str(k): str(v) for k, v in (pick.get("channels") or {}).items()}
    typed = answer.strip()
    match = next((label for label in names if _same(label, typed)), None)
    channel_id, channel_name = (names[match], match) if match else (typed.lstrip("#"), typed.lstrip("#"))
    if not channel_name or " " in channel_name:
        return None
    return Draft(str(pick.get("tool") or ""), channel_id, channel_name.lstrip("#"), str(pick.get("text") or ""))


def _same(a: str, b: str) -> bool:
    return a.strip().lstrip("#").casefold() == b.strip().lstrip("#").casefold()


# --- the card and the walk ---------------------------------------------------------------------------------

def card(draft: Draft) -> tuple[str, dict]:
    header = f"Post this narrative to {draft.label}?"
    if len(header.encode("utf-8")) > _HEADER_MAX:
        header = "Post this narrative?"
    hitl = question_metadata(header, [(POST, "Post exactly this text, once."), (SKIP, "Don't post it.")],
                             multi_select=False, allow_custom_input=True)
    body = "\n".join(f"> {line}" if line else ">" for line in draft.text.splitlines())
    text = (f"Channel: {draft.label}\nVia: {draft.tool}\n\n{body}\n\nReply Post or Skip, or type the edited text to "
            "replace the draft.")
    return text, hitl


def interpret(answer: str) -> tuple[str, str]:
    """post / skip / edit (the typed text replaces the draft) / unclear. Empty is skip: nothing by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _NO:
        return "skip", ""
    if word in _YES:
        return "post", ""
    if len(text.split()) >= MIN_EDIT_WORDS:
        return "edit", text
    return "unclear", text


def start(draft: Draft, preamble: str = "", envelope: dict | None = None) -> Outcome:
    return _ask(draft, preamble, envelope)


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(WALK_KEY) if metadata else None
    if not isinstance(stored, Mapping):
        return Outcome("complete", NOT_WAITING)
    draft = Draft.from_dict(stored.get("draft") or {})
    if stored.get("stage") == "gateway":
        approval = Approval.from_dict(stored.get("approval"))
        if approval is None or not stored.get("loop"):  # fail closed: never guess what was approved
            return _done(draft, "not_requested", "Not posted: the pause state was incomplete.")
        return _after(draft, approval, await resume_send(deps, approval, stored["loop"], answer))
    decision, typed = interpret(answer)
    if decision == "skip":
        return _done(draft, "skipped", "Skipped: nothing was posted. Say \"post it\" if you change your mind.")
    if decision == "edit":
        edited = replace(draft, text=typed)
        return _ask(edited, "Here is your edited text. It replaces the draft once you approve it.", None,
                    remember={"narrative": typed, "edited": True})
    if decision == "unclear":
        return _ask(draft, "Reply Post or Skip, or type the full edited text.")
    approval = Approval(draft.tool, draft.channel_id, draft.text)
    log.info("post to %s approved", draft.label)
    return _after(draft, approval, await send_one(deps, approval))


def _ask(draft: Draft, preamble: str = "", envelope: dict | None = None, remember: dict | None = None) -> Outcome:
    question, hitl = card(draft)
    text = "\n\n".join(p for p in (preamble, question) if p)
    metadata = {WALK_KEY: {"draft": draft.to_dict(), "stage": "approve", "approval": None, "loop": None}}
    return Outcome("input_required", text, metadata, hitl, remember=remember, envelope=envelope)


def _after(draft: Draft, approval: Approval, result: SendResult) -> Outcome:
    if result.status == "paused":
        metadata = {WALK_KEY: {"draft": draft.to_dict(), "stage": "gateway", "approval": approval.to_dict(),
                               "loop": result.loop}}
        return Outcome("auth_required", f"The platform needs your approval before this is posted to {draft.label}.",
                       metadata, {"hitl_request_id": result.hitl_request_id})
    if result.status == "sent":
        link = f" ({result.link})" if result.link else ""
        return _done(draft, "posted", f"Posted to {draft.label}{link}.", result.link)
    if result.status == "denied":
        return _done(draft, "skipped", "The post was denied, so nothing was posted and I won't retry it.")
    reason = ("Posting is blocked for this agent." if result.status == "blocked"
              else f"Not posted: {result.reason or 'unknown reason'}.")
    return _done(draft, "copyable", f"{reason} Here is the text to copy:\n\n{render.copyable(draft.text)}")


def _done(draft: Draft, state: str, text: str, link: str | None = None) -> Outcome:
    return Outcome("complete", text, remember={"post": {"channel": draft.label if draft.channel_name else None,
                                                        "state": state, "link": link},
                                               "narrative": draft.text or None})
