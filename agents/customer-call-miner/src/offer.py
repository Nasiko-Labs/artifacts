"""What the turn offers to write, decided in code: the CRM notes (when the CRM was read and a note tool
is granted) and the digest (only when the user named a channel, verified with a chat read)."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import approvals as ap
import coverage
import digest as dg
import notes as nt
import prompts
from deps import TurnDeps
from discovery import Catalog, discover
from mining import accounts_of, calls_of, window_of
from nasiko_mcp import McpTool
from reader import Reader
from records import item_from, load_json_object, text_of
from walk import DIGEST_KEY, Walk

log = logging.getLogger("call_miner.offer")

CHANNEL_LOOP_CALLS = 2
_HASH = re.compile(r"(?<![\w&])#([A-Za-z0-9][\w.-]{0,79})")
_NAMED = re.compile(r"\b(?:to|in|on) (?:the )?([A-Za-z0-9][\w.-]{0,79}) channel\b|\bchannel ([A-Za-z0-9][\w.-]{0,79})",
                    re.IGNORECASE)
_NOTE_TOKENS = frozenset({"note", "notes", "engagement", "engagements"})
_CREATE_TOKENS = frozenset({"create", "add", "write", "post"})
_ASSOC_TOKENS = frozenset({"associate", "association", "associations"})
_POST_TOKENS = frozenset({"post", "send"})
_MESSAGE_TOKENS = frozenset({"message", "messages", "chat", "msg"})
_NOT_A_CHANNEL_POST = frozenset({"schedule", "scheduled", "ephemeral", "me", "dm", "im", "reminder", "draft", "thread"})


def channel_from(text: str) -> str:
    """The channel the user named ("#product-feedback", "to the product channel"), or ""."""
    match = _HASH.search(text or "")
    if match:
        return match.group(1).rstrip(".")
    match = _NAMED.search(text or "")
    return (match.group(1) or match.group(2)).rstrip(".") if match else ""


async def plan(state: Mapping[str, Any], deps: TurnDeps, mode: str, single: str = "") -> tuple[Walk, str]:
    """The walk to start (possibly empty) and the extra lines for the reply."""
    lines: list[str] = []
    walk = Walk()
    if mode in ("all", "notes", "single"):
        walk, line = await _notes(state, deps, walk, single)
        lines += [line] if line else []
    channel = text_of(state.get("channel"))
    if mode in ("all", "digest") and channel:
        digest, line = await _digest(state, deps, channel)
        walk = replace(walk, digest=digest)
        lines += [line] if line else []
    elif mode == "all" and state.get("items"):
        lines.append("To share a digest, say: post the digest to #your-channel.")
    elif mode == "digest":
        lines.append(prompts.NO_CHANNEL)
    return walk, "\n\n".join(lines)


async def _notes(state: Mapping[str, Any], deps: TurnDeps, walk: Walk, single: str) -> tuple[Walk, str]:
    if not state.get("arr_available"):
        return walk, ""
    written = {k for k, v in (state.get("written") or {}).items() if v == "sent"}
    drafts = nt.build_notes([item_from(i) for i in state.get("items") or ()], calls_of(state), accounts_of(state),
                            window_of(state, deps).start.isoformat(), frozenset(written))
    if single:
        drafts = tuple(d for d in drafts if d.name.casefold() == single.casefold())[:1]
        if not drafts:
            return walk, f"I have no unwritten note for {single}."
    if not drafts:
        return walk, ""
    catalog, failure = await discover(deps, "notes")
    note_tool = _pick(catalog, (_NOTE_TOKENS, _CREATE_TOKENS)) if catalog else None
    if note_tool is None:
        log.info("no note tool: %s", failure)
        return walk, "No tool to add CRM notes is available to this agent, so no notes are offered."
    assoc = _pick(catalog, (_ASSOC_TOKENS,))
    stage = "single" if single else "notes"
    return Walk(notes=drafts, note_tool=note_tool.name, assoc_tool=assoc.name if assoc else "", stage=stage), ""


def _pick(catalog: Catalog, wanted: tuple[frozenset[str], ...], banned: frozenset[str] = frozenset()) -> McpTool | None:
    return catalog.write_tool(*wanted, banned=banned)


async def _digest(state: Mapping[str, Any], deps: TurnDeps, channel: str) -> tuple[ap.DigestPlan | None, str]:
    if (state.get("written") or {}).get(DIGEST_KEY) == "sent":
        return None, "The digest for this period was already posted."
    text = dg.digest_text(window_of(state, deps).label, [item_from(i) for i in state.get("items") or ()],
                          state.get("groupings") or {}, accounts_of(state), bool(state.get("arr_available")),
                          deps.config.digest_max_chars)
    catalog, failure = await discover(deps, "digest")
    if catalog is None:
        return None, f"{failure}\n\nDigest to copy:\n\n{text}"
    reader = Reader.for_catalog(deps, catalog)
    post = _pick(catalog, (_POST_TOKENS, _MESSAGE_TOKENS), _NOT_A_CHANNEL_POST)
    if not reader.tools or post is None:
        status = reader.status() if reader.tools else coverage.SourceStatus("chat", "missing")
        return None, f"{coverage.line(status)}\n\nDigest to copy:\n\n{text}"
    found = await _verify_channel(deps, reader, channel)
    if found is None:
        return None, f"I couldn't find the channel #{channel.lstrip('#')}, so nothing will be posted. Digest to copy:\n\n{text}"
    return ap.DigestPlan(channel_id=found[0], channel_name=found[1], text=text, tool=post.name), ""


async def _verify_channel(deps: TurnDeps, reader: Reader, channel: str) -> tuple[str, str] | None:
    """The channel's id and name, both present in chat tool output; the name must be the one asked for."""
    messages = ({"role": "system", "content": prompts.channel_system(channel)},
                {"role": "user", "content": f"Find the channel {channel}."})
    result = await reader.loop(deps, messages, CHANNEL_LOOP_CALLS)
    data = load_json_object(result.text) or {}
    channel_id, name = text_of(data.get("channel_id")), text_of(data.get("name")).lstrip("#")
    seen = reader.seen_text
    wanted = channel.lstrip("#").casefold()
    if not channel_id or channel_id not in seen or not name or name.casefold() not in seen.casefold():
        return None
    if wanted not in (name.casefold(), channel_id.casefold()):
        return None
    return channel_id, name
