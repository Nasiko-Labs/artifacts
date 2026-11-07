"""The chat reads, made by code (never by the model): one search per query, then the replies of the
shortlisted threads. Without a usable message search, a narrower history scan: the latest 200 messages
of up to 5 channels (the ones named, else the busiest), filtered in code. Every call goes through the
turn's read-only, budgeted, health-checked gateway, and every coverage gap becomes one line."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta

import chatdata
import toolargs
from deps import TurnDeps
from discovery import Catalog
from models import Hit, Message, Thread
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from terms import terms
from tool_health import ToolHealth

log = logging.getLogger("dedup.search")

MAX_SEARCHES = 8
MAX_SCAN_CHANNELS = 5
DAYS_PER_MONTH = 30.44


@dataclass(frozen=True)
class Reach:
    hits: tuple[Hit, ...]
    coverage: tuple[str, ...] = ()
    narrower: bool = False
    available: bool = True  # False: no chat source could be searched at all


async def _call(health: ToolHealth, deps: TurnDeps, tool: McpTool, args: dict) -> str | None:
    """The tool's text, or None when the call failed."""
    try:
        result = await health.call_tool(tool.name, args, deps.traceparent)
    except McpGatewayError as e:
        log.warning("%s failed: %s", tool.name, type(e).__name__)
        return None
    if not isinstance(result, ToolOk) or result.is_error:
        return None
    return result.text


def _in_window(message: Message, deps: TurnDeps, months: int) -> bool:
    oldest = (deps.now - timedelta(days=months * DAYS_PER_MONTH)).timestamp()
    return message.time is not None and oldest <= message.time <= deps.now.timestamp()


def _in_scope(message: Message, channels: Sequence[str]) -> bool:
    if not channels:
        return True
    return message.channel_name.lstrip("#").casefold() in channels or message.channel_id.casefold() in channels


async def find_hits(deps: TurnDeps, catalog: Catalog, health: ToolHealth, queries: Sequence[str],
                    channels: Sequence[str], months: int) -> Reach:
    tool = catalog.role("search")
    probe = toolargs.search_args(tool, queries[0]) if tool else None
    if tool is None or probe is None:
        why = "not available for your chat connection" if tool is None else "not usable (unexpected arguments)"
        return await _scan(deps, catalog, health, queries, channels, months, f"Message search is {why}")
    plan = [(q, toolargs.search_args(tool, q, c)) for q in queries for c in (channels or (None,))][:MAX_SEARCHES]
    first = await _call(health, deps, tool, plan[0][1])
    if not health.is_offered(tool.name):
        reason = health.dropped.get(tool.name, "unavailable")
        return await _scan(deps, catalog, health, queries, channels, months, f"Message search: unavailable ({reason})")
    rest = await asyncio.gather(*(_call(health, deps, tool, args) for _, args in plan[1:]))
    hits: dict[tuple[str, str], Hit] = {}
    for (query, _), text in zip(plan, (first, *rest), strict=True):
        for message in chatdata.messages_in(text or ""):
            if not (_in_window(message, deps, months) and _in_scope(message, channels)):
                continue
            key = (message.channel_id, message.ts)
            old = hits.get(key)
            hits[key] = Hit(message, (old.queries if old else frozenset()) | {query})
    failed = sum(1 for t in (first, *rest) if t is None)
    coverage = (f"{failed} of {len(plan)} searches failed.",) if failed else ()
    return Reach(tuple(hits.values()), coverage, narrower=bool(failed))


async def _scan(deps: TurnDeps, catalog: Catalog, health: ToolHealth, queries: Sequence[str],
                channels: Sequence[str], months: int, why: str) -> Reach:
    history = catalog.role("history")
    if history is None:
        return Reach((), (f"{why}, and no tool reads channel history, so nothing was searched.",), True, False)
    picked = await _scan_channels(deps, catalog, health, channels)
    if not picked:
        return Reach((), (f"{why}, and I couldn't list your channels to scan instead.",), True, False)
    oldest = f"{(deps.now - timedelta(days=months * DAYS_PER_MONTH)).timestamp():.0f}"
    planned = [(cid, name, toolargs.history_args(history, cid, oldest)) for cid, name in picked]
    planned = [(cid, name, args) for cid, name, args in planned if args is not None]
    texts = await asyncio.gather(*(_call(health, deps, history, args) for _, _, args in planned))
    query_terms = [(q, terms(q)) for q in queries]
    hits: list[Hit] = []
    for (cid, name, _), text in zip(planned, texts, strict=True):
        for message in chatdata.messages_in(text or "", cid, name):
            words = terms(message.text)
            matched = frozenset(q for q, qt in query_terms if qt and len(qt & words) >= min(2, len(qt)))
            if matched and _in_window(message, deps, months):
                hits.append(Hit(replace(message, channel_id=cid, channel_name=message.channel_name or name), matched))
    labels = ", ".join(f"#{name}" if name else cid for cid, name, _ in planned)
    note = f"{why}, so I scanned only the latest 200 messages of {len(planned)} channels ({labels})."
    return Reach(tuple(hits), (note,), narrower=True)


async def _scan_channels(deps: TurnDeps, catalog: Catalog, health: ToolHealth,
                         named: Sequence[str]) -> tuple[tuple[str, str], ...]:
    lister = catalog.role("channels")
    args = toolargs.channels_args(lister) if lister else None
    listed = chatdata.channels_in(await _call(health, deps, lister, args) or "") if args is not None else ()
    if named:
        by_name = {name.casefold(): (cid, name) for cid, name in listed}
        return tuple(by_name.get(n, (n, n)) for n in named)[:MAX_SCAN_CHANNELS]
    return listed[:MAX_SCAN_CHANNELS]


async def read_threads(deps: TurnDeps, catalog: Catalog, health: ToolHealth,
                       threads: Sequence[Thread]) -> tuple[tuple[Thread, ...], tuple[str, ...]]:
    """The shortlisted threads with their replies read; a thread that could not be read keeps the messages
    the search found."""
    if not threads:
        return (), ()
    tool = catalog.role("replies")
    if tool is None:
        return tuple(threads), ("No tool reads thread replies, so only the matching messages were checked.",)
    if deps.past_deadline():
        return tuple(threads), ("Thread replies were not read (time limit).",)
    planned = [(t, toolargs.replies_args(tool, t.channel_id, t.root_ts)) for t in threads]
    texts = await asyncio.gather(*(_call(health, deps, tool, args) if args else _none() for t, args in planned))
    out, failed = [], 0
    for (thread, _), text in zip(planned, texts, strict=True):
        if text is None:
            failed += 1
            out.append(thread)
            continue
        out.append(_with_replies(thread, chatdata.messages_in(text, thread.channel_id, thread.channel_label)))
    notes = ()
    if failed:
        reason = health.dropped.get(tool.name)
        why = f" ({reason})" if reason else ""
        notes = ((f"{failed} of {len(planned)} threads could not be read{why}; those were checked on the matching "
                  "messages only."),)
    return tuple(out), notes


async def _none() -> None:
    return None


def _with_replies(thread: Thread, messages: Sequence[Message]) -> Thread:
    by_ts = {m.ts: m for m in thread.messages}
    for message in messages:
        by_ts[message.ts] = replace(message, channel_id=thread.channel_id,
                                    channel_name=message.channel_name or thread.channel_label.lstrip("#"))
    root = by_ts.pop(thread.root_ts, thread.root)
    replies = tuple(sorted(by_ts.values(), key=lambda m: m.time or 0.0))
    return replace(thread, root=root, replies=replies)
