"""Before the approval pause: fix where the rollup goes, from checked data only. A document gets the
title code set and the one create tool code picked; a chat post gets a channel id that a read call
returned verbatim for the name the user gave, and the one post tool code picked."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

import prompts
import render
from approval import WriteRequest
from deps import TurnDeps
from discovery import discover
from reader import Reader
from verbatim import appears_in, load_json_object, normalise_text, text_of

log = logging.getLogger("commitment_audit.targets")

CHANNEL_LOOP_CALLS = 3


@dataclass(frozen=True)
class Prepared:
    reply: str
    request: WriteRequest | None = None


async def prepare(deps: TurnDeps, target: str, rollup: str, title: str, written: Mapping[str, str]) -> Prepared:
    if target.casefold().startswith("chat"):
        name = target.split(":", 1)[1].strip().lstrip("#").strip() if ":" in target else ""
        if not name:
            return Prepared("Which chat channel should I post the rollup to? Say \"post it to #channel\".")
        return await _chat(deps, name, rollup, written)
    return await _doc(deps, rollup, title, written)


async def _doc(deps: TurnDeps, rollup: str, title: str, written: Mapping[str, str]) -> Prepared:
    if written.get("doc") == "written":
        return Prepared("This rollup is already saved as a document. Change it first, or ask for a new audit.")
    catalog, failure = await discover(deps, "write_doc")
    if failure is not None:
        return Prepared(failure)
    tool = catalog.write_tool("document")
    if tool is None:
        return Prepared(render.render_copyable("No document-create tool is available to this agent.", rollup))
    return Prepared("", WriteRequest(kind="doc", tool=tool.name, body=rollup, title=title))


async def _chat(deps: TurnDeps, name: str, rollup: str, written: Mapping[str, str]) -> Prepared:
    channel_id, failure = await _resolve_channel(deps, name)
    if channel_id is None:
        return Prepared(failure)
    if written.get(f"chat:{channel_id}") == "written":
        return Prepared(f"This rollup is already posted to #{name}. Change it first, or ask for a new audit.")
    catalog, failure = await discover(deps, "write_chat")
    if failure is not None:
        return Prepared(failure)
    tool = catalog.write_tool("chat")
    if tool is None:
        return Prepared(render.render_copyable("No chat-post tool is available to this agent.", rollup))
    return Prepared("", WriteRequest(kind="chat", tool=tool.name, body=rollup, channel_id=channel_id,
                                     channel_name=name))


async def _resolve_channel(deps: TurnDeps, name: str) -> tuple[str | None, str]:
    not_found = f"I couldn't find a chat channel named #{name} that I can confirm, so nothing will be posted."
    catalog, failure = await discover(deps, "channel")
    if failure is not None:
        return None, failure
    tools = catalog.read_tools(("chat",))
    if not tools:
        return None, "No chat tool is available to this agent, so I can't post the rollup."
    reader = Reader(deps)
    step = await reader.run(deps, prompts.channel_system(name), f"Find the channel #{name}.", tools,
                            CHANNEL_LOOP_CALLS, load_json_object)
    channel_id = text_of((step.parsed or {}).get("channel_id"))
    seen = normalise_text(reader.seen_text)
    # The id must come from a tool, and that tool output must name this channel.
    if not appears_in(channel_id, seen) or not appears_in(name, seen):
        log.info("channel %r not confirmed by tool output", name)
        return None, not_found
    return channel_id, ""
