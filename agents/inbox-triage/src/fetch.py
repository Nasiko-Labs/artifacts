"""The read side of a turn: the user's own address, the inbox search (one bounded tool-loop step, then
pagination in code), and thread reads for the ambiguous or truncated threads. Every call goes through
the budget, the tool-health tracker and the fence, in that order."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import mailparse
import prompts
from deps import LLM_CALL_TIMEOUT_SECS, TurnDeps
from discovery import Catalog, name_tokens
from fence import FencedGateway
from models import Mail
from nasiko_mcp import McpGatewayError, McpTool, ToolOk, ToolResult
from signals import address_of
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("inbox_triage.fetch")

MAX_TOOL_CALLS = 14
MAX_SEARCH_CALLS = 3
MAX_THREAD_READS = 10
SEARCH_PAGE_SIZE = 100
MAX_THREADS_FETCHED = 200
_PAUSED = ("approval_pending", "authorization_required")
_PAGE_PARAM = re.compile(r"page_?token|cursor", re.IGNORECASE)
_THREAD_PARAM = re.compile(r"^(?:thread_?id|id)$", re.IGNORECASE)


class Budget:
    """Caps real tool calls per turn; a call past the cap is answered with a note, never forwarded."""

    def __init__(self, inner: Any, limit: int = MAX_TOOL_CALLS):
        self._inner = inner
        self._limit = limit
        self.used = 0

    @property
    def left(self) -> int:
        return self._limit - self.used

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self.used >= self._limit:
            return ToolOk(content=({"type": "text", "text": '{"error": "tool-call limit for this turn reached"}'},),
                          is_error=True, raw={})
        self.used += 1
        return await self._inner.call_tool(name, arguments, traceparent)


@dataclass
class Reader:
    """One turn's read side (never persisted)."""

    catalog: Catalog
    fenced: FencedGateway
    health: ToolHealth
    budget: Budget

    @staticmethod
    def build(deps: TurnDeps, catalog: Catalog) -> Reader:
        fenced = FencedGateway(deps.gateway)
        health = ToolHealth(fenced)
        return Reader(catalog=catalog, fenced=fenced, health=health, budget=Budget(health))

    def offered(self) -> tuple[McpTool, ...]:
        return tuple(t for t in self.catalog.tools if self.health.is_offered(t.name))


@dataclass(frozen=True)
class Listing:
    mails: tuple[Mail, ...] = ()
    me: tuple[str, ...] = ()
    reply: str | None = None  # a fixed reply that ends the turn
    budget_hit: bool = False


async def read_inbox(deps: TurnDeps, reader: Reader, query: str) -> Listing:
    me = await _profile(deps, reader)
    messages, failure = await _search(deps, reader, query)
    if failure is not None:
        return Listing(reply=failure)
    sent_by_me = {address_of(m.sender) for m in messages if "SENT" in {x.upper() for x in m.labels}}
    me = tuple(dict.fromkeys((*me, *(a for a in sent_by_me if a))))
    return Listing(mails=tuple(mailparse.threads_of(messages, me)), me=me, budget_hit=reader.budget.left <= 0)


async def _profile(deps: TurnDeps, reader: Reader) -> tuple[str, ...]:
    tool = next((t for t in reader.catalog.tools if "profile" in name_tokens(t.name) and not _required(t)), None)
    if tool is None:
        return ()
    try:
        await reader.budget.call_tool(tool.name, {}, deps.traceparent)
    except McpGatewayError as e:
        log.info("profile read failed: %s", type(e).__name__)
        return ()
    return _profile_from(reader)


def _profile_from(reader: Reader) -> tuple[str, ...]:
    record = next((r for r in reversed(reader.fenced.records) if "profile" in name_tokens(r.name)), None)
    return mailparse.profile_addresses(record.text) if record else ()


async def _search(deps: TurnDeps, reader: Reader, query: str) -> tuple[list[mailparse.Message], str | None]:
    tools = tuple(t for t in reader.offered() if "profile" not in name_tokens(t.name))
    if not tools:
        return [], prompts.PASTE_PROMPT
    messages_ = ({"role": "system", "content": prompts.list_system(deps.now, query, SEARCH_PAGE_SIZE)},
                 {"role": "user", "content": "Fetch my inbox for triage."})
    start = len(reader.fenced.records)
    result = await run_tool_loop(deps.llm, deps.model, messages_, tools, reader.budget, deps.traceparent,
                                 max_iterations=1, is_offered=reader.health.is_offered, timeout=LLM_CALL_TIMEOUT_SECS)
    if result.status in _PAUSED:
        return [], prompts.READ_NEEDS_APPROVAL
    found = _found(reader, start)
    still_offered = any(reader.health.is_offered(t.name) for t in tools)
    if not found and still_offered and reader.budget.left > 0:  # one more step: the model saw the failure (or called nothing)
        history = result.messages if result.tools_called else (*result.messages, {
            "role": "user", "content": "Call the email search tool now with the query given."})
        again = await run_tool_loop(deps.llm, deps.model, history, tools, reader.budget, deps.traceparent,
                                    max_iterations=1, is_offered=reader.health.is_offered,
                                    timeout=LLM_CALL_TIMEOUT_SECS)
        if again.status in _PAUSED:
            return [], prompts.READ_NEEDS_APPROVAL
        found = _found(reader, start)
    if not found:
        return [], None
    return await _paginate(deps, reader, found), None


def _found(reader: Reader, start: int) -> list:
    """(record, messages) for each call since `start` that returned messages."""
    out = []
    for record in reader.fenced.records[start:]:
        msgs = tuple(mailparse.messages_in(record.text))
        if msgs:
            out.append((record, msgs))
    return out


async def _paginate(deps: TurnDeps, reader: Reader, found: list) -> list[mailparse.Message]:
    record, _ = found[0]
    messages = [m for _, msgs in found for m in msgs]
    tool = reader.catalog.by_name(record.name)
    param = _page_param(tool) if tool else None
    token = mailparse.next_token(record.text)
    calls = len(found)
    while token and param and calls < MAX_SEARCH_CALLS and len(messages) < MAX_THREADS_FETCHED:
        if reader.budget.left <= MAX_THREAD_READS // 2:
            break
        arguments = {**dict(record.arguments), param: token}
        try:
            result = await reader.budget.call_tool(record.name, arguments, deps.traceparent)
        except McpGatewayError as e:
            log.info("next page failed: %s", type(e).__name__)
            break
        calls += 1
        latest = reader.fenced.records[-1] if reader.fenced.records else None
        if not isinstance(result, ToolOk) or result.is_error or latest is None or latest.arguments != arguments:
            break
        page = mailparse.messages_in(latest.text)
        if not page:
            break
        messages.extend(page)
        token = mailparse.next_token(latest.text)
    return messages


def _page_param(tool: McpTool) -> str | None:
    props = tool.input_schema.get("properties")
    if not isinstance(props, Mapping):
        return None
    return next((name for name in props if _PAGE_PARAM.search(str(name))), None)


def _required(tool: McpTool) -> Sequence[str]:
    req = tool.input_schema.get("required")
    return req if isinstance(req, list) else ()


def thread_tool(reader: Reader) -> tuple[McpTool, str] | None:
    """A tool that reads one whole thread by its id, and the argument that carries the id."""
    for tool in reader.offered():
        if "thread" not in name_tokens(tool.name):
            continue
        props = tool.input_schema.get("properties")
        if not isinstance(props, Mapping):
            continue
        param = next((p for p in props if _THREAD_PARAM.match(str(p))), None)
        if param:
            return tool, param
    return None


async def read_threads(deps: TurnDeps, reader: Reader, mails: Sequence[Mail], me: Sequence[str]) -> dict[str, Mail]:
    """Whole threads for up to MAX_THREAD_READS of `mails` (ambiguous or truncated), within the budget."""
    found = thread_tool(reader)
    if found is None:
        return {}
    tool, param = found
    out: dict[str, Mail] = {}
    for mail in list(mails)[:MAX_THREAD_READS]:
        if reader.budget.left <= 0 or not reader.health.is_offered(tool.name) or deps.out_of_time():
            break
        try:
            result = await reader.budget.call_tool(tool.name, {param: mail.id}, deps.traceparent)
        except McpGatewayError as e:
            log.info("thread read failed: %s", type(e).__name__)
            continue
        latest = reader.fenced.records[-1] if reader.fenced.records else None
        if not isinstance(result, ToolOk) or result.is_error or latest is None or latest.arguments != {param: mail.id}:
            continue
        threads = [t for t in mailparse.threads_of(mailparse.messages_in(latest.text), me) if t.id == mail.id]
        if threads:
            out[mail.id] = threads[0]
    return out
