"""Optional trust evidence: chat messages that name a dashboard and complain about it. Code drives the search
(one call per dashboard, capped) through the chat search tool discovery found, filling the tool's own query
argument from its schema; no model call is involved. Matching (exact dashboard name plus a negative cue, within
30 days) and the latest verbatim quote are code. A quote or link that is not in the search output is dropped."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import coverage
from deps import TurnDeps
from discovery import name_tokens
from models import Complaints, Dashboard
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from sheet import Reader, appears_in
from tool_health import failure_text
from values import parse_date

log = logging.getLogger("trust_checker.complaints")

MAX_SEARCHES = 25
CONCURRENCY = 5
WINDOW_DAYS = 30
MAX_QUOTE_CHARS = 300
NEGATIVE = re.compile(r"\b(?:wrong|broken|doesn'?t match|does not match|stale|off|numbers look|not updating)\b",
                      re.IGNORECASE)
_QUERY_ARGS = ("query", "q", "search", "search_query", "text", "keyword", "keywords", "term")
_SEARCH_WORDS = ("search", "find", "query")
_CHAT_NAME_WORDS = frozenset({"message", "messages", "chat", "chats", "channel", "channels", "conversation",
                              "conversations", "thread", "threads"})
_MESSAGES = re.compile(r"\bmessages?\b", re.IGNORECASE)
_TEXT_KEYS = ("text", "message", "body", "content")
_WHEN_KEYS = ("ts", "timestamp", "date", "created", "created_at", "time", "sent_at")
_LINK_KEYS = ("permalink", "link", "url")
_EPOCH = re.compile(r"^\d{9,10}(?:\.\d+)?$")


@dataclass(frozen=True)
class Message:
    text: str
    when: date | None = None
    link: str | None = None


@dataclass(frozen=True)
class Scan:
    evidence: Mapping[str, Complaints] = field(default_factory=dict)
    status: coverage.SourceStatus = field(default_factory=lambda: coverage.SourceStatus("chat", "missing"))
    searched: tuple[str, ...] = ()
    dropped: int = 0

    @property
    def ok(self) -> bool:
        return self.status.ok

    def to_dict(self) -> dict:
        return {"evidence": {k: v.to_dict() for k, v in self.evidence.items()},
                "status": {"source": "chat", "state": self.status.state, "reason": self.status.reason},
                "searched": list(self.searched), "dropped": self.dropped}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Scan:
        data = data or {}
        raw = data.get("status") or {}
        status = coverage.SourceStatus("chat", raw.get("state") or "missing", raw.get("reason") or "")
        return Scan({str(k): Complaints.from_dict(v) for k, v in (data.get("evidence") or {}).items()}, status,
                    tuple(data.get("searched") or ()), int(data.get("dropped") or 0))


def search_tool(tools: Sequence[McpTool]) -> tuple[McpTool, str] | None:
    """The chat tool that searches messages, and the name of its query argument; None when no tool can be
    called with just a query (another required argument code cannot fill)."""
    for tool in tools:
        text = f"{tool.name} {tool.description}".casefold()
        if not any(w in text for w in _SEARCH_WORDS) or not _searches_messages(tool):
            continue
        arg = _query_arg(tool.input_schema)
        if arg is not None:
            return tool, arg
    return None


def _searches_messages(tool: McpTool) -> bool:
    """The tool's own name says chat, or its description says it handles messages: a file search tool found by a
    chat query is not a chat search."""
    return bool(set(name_tokens(tool.name)) & _CHAT_NAME_WORDS) or bool(_MESSAGES.search(tool.description))


def _query_arg(schema: Mapping[str, Any]) -> str | None:
    props = schema.get("properties") if isinstance(schema, Mapping) else None
    props = props if isinstance(props, Mapping) else {}
    required = [r for r in (schema.get("required") or []) if isinstance(r, str)] if isinstance(schema, Mapping) else []
    strings = [k for k, v in props.items() if isinstance(v, Mapping) and v.get("type") in ("string", None)]
    arg = next((k for k in _QUERY_ARGS if k in strings), None) or next((k for k in required if k in strings), None)
    if arg is None or any(r != arg for r in required):
        return None
    return arg


async def scan(deps: TurnDeps, reader: Reader | None, dashboards: Sequence[Dashboard]) -> Scan:
    if reader is None:
        return Scan()
    tools = reader.tools_for("chat")
    status = reader.status("chat")
    found = search_tool(tools) if tools else None
    if found is None:
        return Scan(status=status if not status.ok else coverage.SourceStatus("chat", "missing"))
    tool, arg = found
    names = _targets(dashboards)
    gate = asyncio.Semaphore(CONCURRENCY)
    outputs = await asyncio.gather(*(_search(deps, reader, tool.name, arg, n, gate) for n in names))
    status = reader.status("chat") if tool.name not in reader.health.dropped else coverage.SourceStatus(
        "chat", "unavailable", reader.health.dropped[tool.name])
    if not status.ok:
        return Scan(status=status)
    parsed = [messages_from(o or "") for o in outputs]
    # The raw output, plus each message's decoded text (JSON escapes quotes and non-ASCII characters).
    seen = "\n".join([*(o for o in outputs if o), *(m.text for ms in parsed for m in ms)])
    evidence, dropped = {}, 0
    for name, messages in zip(names, parsed, strict=True):
        found_for = evidence_for(name, messages, deps.now.date())
        kept, lost = verify(found_for, seen)
        dropped += lost
        if kept.count:
            evidence = {**evidence, name: kept}
    return Scan(evidence, status, tuple(names), dropped)


def _targets(dashboards: Sequence[Dashboard]) -> list[str]:
    """Most-viewed first, unique names, capped."""
    ordered = sorted(dashboards, key=lambda d: -(d.viewers or 0))
    return list(dict.fromkeys(d.name for d in ordered))[:MAX_SEARCHES]


async def _search(deps: TurnDeps, reader: Reader, tool: str, arg: str, name: str, gate: asyncio.Semaphore) -> str:
    async with gate:
        if tool in reader.health.dropped:
            return ""
        try:
            result = await reader.health.call_tool(tool, {arg: f'"{name}"'}, deps.traceparent)
        except McpGatewayError as e:
            log.warning("chat search for one dashboard failed: %s", type(e).__name__)
            return ""
    if not isinstance(result, ToolOk) or failure_text(result) is not None:
        return ""
    return _raw_text(result)


def _raw_text(result: ToolOk) -> str:
    structured = result.raw.get("structuredContent")
    if isinstance(structured, Mapping):
        return json.dumps(structured, default=str)
    content = result.raw.get("content") or []
    return "\n".join(str(c.get("text") or "") for c in content if isinstance(c, Mapping))


def messages_from(text: str) -> list[Message]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return [Message(line.strip()) for line in (text or "").splitlines() if line.strip()]
    return list(_walk(data))


def _walk(value: Any):
    if isinstance(value, Mapping):
        message = _message(value)
        if message is not None:
            yield message
        for v in value.values():
            if isinstance(v, (Mapping, list)):
                yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def _message(item: Mapping[str, Any]) -> Message | None:
    text = next((item[k] for k in _TEXT_KEYS if isinstance(item.get(k), str) and item[k].strip()), None)
    if text is None:
        return None
    when = next((w for w in (_when(item.get(k)) for k in _WHEN_KEYS) if w is not None), None)
    link = next((item[k] for k in _LINK_KEYS if isinstance(item.get(k), str) and item[k].startswith("http")), None)
    return Message(" ".join(text.split()), when, link)


def _when(raw: Any) -> date | None:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        raw = str(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    if _EPOCH.match(raw.strip()):
        return datetime.fromtimestamp(float(raw), UTC).date()
    return parse_date(raw)


def evidence_for(name: str, messages: Sequence[Message], today: date) -> Complaints:
    """Messages naming the dashboard exactly, with a negative cue, within the window; the latest quoted."""
    pattern = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])", re.IGNORECASE)
    matching: dict[tuple[str, str | None], Message] = {}
    for m in messages:
        recent = m.when is None or 0 <= (today - m.when).days <= WINDOW_DAYS
        if recent and pattern.search(m.text) and NEGATIVE.search(m.text):
            matching.setdefault((m.text, m.link), m)
    if not matching:
        return Complaints()
    latest = max(matching.values(), key=lambda m: m.when or date.min)
    return Complaints(len(matching), latest.text[:MAX_QUOTE_CHARS], latest.link)


def verify(found: Complaints, seen: str) -> tuple[Complaints, int]:
    """Drop a quote or link that is not verbatim in this turn's search output; count what was dropped."""
    quote_ok = found.latest_quote is None or appears_in(found.latest_quote, seen)
    link_ok = found.link is None or found.link in seen
    dropped = (0 if quote_ok else 1) + (0 if link_ok else 1)
    return Complaints(found.count, found.latest_quote if quote_ok else None, found.link if link_ok else None), dropped
