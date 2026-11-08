"""The reads, made by code (never by the model): the dashboard index from a spreadsheet, and recent
top-level messages of a chat channel. Every call goes through the turn's read-only, budgeted, recorded,
health-checked gateway; every gap becomes one coverage line."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

import chatdata
import coverage
import index as index_
import toolargs
from chatdata import ChatMessage
from deps import MAX_TOOL_CALLS, TurnDeps
from discovery import Catalog
from fence import CallBudget, ReadOnly, Recorder
from models import Index
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from tool_health import ToolHealth

log = logging.getLogger("deflector.sources")

DEFAULT_INDEX_QUERY = "dashboard"
MAX_SHEET_CHOICES = 8
_CHANNEL_ID = re.compile(r"^[CDG][A-Z0-9]{6,}$")


class Reader:
    """One turn's read side."""

    def __init__(self, deps: TurnDeps, catalog: Catalog):
        self.deps = deps
        self.catalog = catalog
        self.recorder = Recorder(ReadOnly(deps.gateway))
        self.health = ToolHealth(CallBudget(self.recorder, MAX_TOOL_CALLS))

    async def call(self, tool: McpTool, args: dict) -> str | None:
        """The tool's text, or None when the call failed."""
        try:
            result = await self.health.call_tool(tool.name, args, self.deps.traceparent)
        except McpGatewayError as e:
            log.warning("%s failed: %s", tool.name, type(e).__name__)
            return None
        if not isinstance(result, ToolOk) or result.is_error:
            return None
        return result.text

    def failed(self, role: str, tool: McpTool) -> str:
        return coverage.failed(role, tool.name, self.health.dropped)


@dataclass(frozen=True)
class IndexRead:
    index: Index | None
    choices: tuple[tuple[str, str], ...] = ()  # several matching sheets: (id, title) to choose from
    notes: tuple[str, ...] = ()


async def read_index(reader: Reader, wanted: str | None, sheet: tuple[str, str] | None = None) -> IndexRead:
    finder, values = reader.catalog.role("sheet_search"), reader.catalog.role("sheet_read")
    if values is None:
        return IndexRead(None, notes=(coverage.missing("sheet_read" if finder else "sheet_search"),))
    if sheet is None:
        if finder is None:
            return IndexRead(None, notes=(coverage.missing("sheet_search"),))
        args = toolargs.sheet_search_args(finder, wanted or DEFAULT_INDEX_QUERY)
        text = await reader.call(finder, args) if args is not None else None
        if text is None:
            return IndexRead(None, notes=(reader.failed("sheet_search", finder),))
        found = index_.matching_sheets(index_.sheets_in(text), wanted)
        if not found:
            title = f"\"{wanted}\"" if wanted else "like \"Dashboard Index\""
            return IndexRead(None, notes=(f"I found no spreadsheet titled {title}.",))
        if len(found) > 1:
            return IndexRead(None, choices=found[:MAX_SHEET_CHOICES])
        sheet = found[0]
    args = toolargs.sheet_read_args(values, sheet[0])
    text = await reader.call(values, args) if args is not None else None
    table = index_.values_in(text) if text is not None else None
    if table is None:
        note = reader.failed("sheet_read", values) if text is None else f"I couldn't read rows from \"{sheet[1]}\"."
        return IndexRead(None, notes=(note,))
    return IndexRead(index_.from_table(table, sheet[1], "sheet"))


@dataclass(frozen=True)
class ChannelRead:
    messages: tuple[ChatMessage, ...]
    channel_id: str = ""
    label: str = ""
    notes: tuple[str, ...] = ()
    available: bool = True


async def read_channel(reader: Reader, channel: str, days: int) -> ChannelRead:
    history = reader.catalog.role("history")
    label = f"#{channel}"
    if history is None:
        return ChannelRead((), label=label, notes=(coverage.missing("history"),), available=False)
    channel_id, notes = await _resolve(reader, channel)
    if channel_id is None:
        return ChannelRead((), label=label, notes=notes, available=False)
    oldest = f"{(reader.deps.now - timedelta(days=days)).timestamp():.0f}"
    args = toolargs.history_args(history, channel_id, oldest)
    text = await reader.call(history, args) if args is not None else None
    if text is None:
        return ChannelRead((), channel_id, label, (*notes, reader.failed("history", history)), available=False)
    since = float(oldest)
    messages = tuple(m for m in chatdata.messages_in(text)
                     if m.is_top_level and m.time is not None and since <= m.time <= reader.deps.now.timestamp())
    return ChannelRead(messages, channel_id, label, notes)


async def _resolve(reader: Reader, channel: str) -> tuple[str | None, tuple[str, ...]]:
    if _CHANNEL_ID.match(channel.upper()) and channel.upper() == channel:
        return channel, ()
    lister = reader.catalog.role("channels")
    args = toolargs.channels_args(lister) if lister else None
    if lister is None or args is None:
        return channel, ()  # no lister: try the name itself
    text = await reader.call(lister, args)
    if text is None:
        return None, (reader.failed("channels", lister),)
    by_name = {name.casefold().lstrip("#"): cid for cid, name in chatdata.channels_in(text)}
    cid = by_name.get(channel.casefold())
    return (cid, ()) if cid else (None, (f"I couldn't find #{channel} among your chat channels.",))
