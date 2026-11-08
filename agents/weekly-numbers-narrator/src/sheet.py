"""Locate and read the user's metrics spreadsheet: a bounded tool loop offered only spreadsheet read
tools, post-checked in code. The model never copies cells: code reads the rows from the tool output itself
(the largest list of rows any read returned) and types them in table.py. Also the shared read side
(`Reader`) that the channel listing uses."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import coverage
import prompts
import table as tables
from deps import TurnDeps
from discovery import RECOVER_META, Catalog
from fence import FencedGateway
from nasiko_mcp import McpTool
from parse import Request
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("numbers_narrator.sheet")

READ_LOOP_CALLS = 8
LLM_TIMEOUT_SECS = 45.0
MAX_CANDIDATES = 8
_PAUSED = ("approval_pending", "authorization_required")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Reader:
    """One turn's read side: every read tool, the fence adapter that records tool output, and the failure
    tracker in front of it. Each step is offered only its own source's tools."""

    tools: tuple[McpTool, ...]
    fenced: FencedGateway
    health: ToolHealth
    source_of: Callable[[McpTool], str | None]
    helpers: frozenset[str] = frozenset()

    def tools_for(self, source: str) -> tuple[McpTool, ...]:
        own = tuple(t for t in self.tools if t.name not in self.helpers and self.source_of(t) == source)
        extra = tuple(t for t in self.tools if t.name in self.helpers)
        return (*own, *extra) if own else ()

    def status(self, source: str) -> coverage.SourceStatus:
        return coverage.assess(source, self.tools_for(source), self.source_of, self.health.dropped, self.helpers)

    async def loop(self, deps: TurnDeps, source: str, messages: Sequence[Mapping[str, Any]],
                   max_iterations: int) -> LoopResult:
        return await run_tool_loop(deps.llm, deps.model, messages, self.tools_for(source), self.health,
                                   deps.traceparent, max_iterations=max_iterations, is_offered=self.health.is_offered,
                                   timeout=LLM_TIMEOUT_SECS)


def reader_for(deps: TurnDeps, catalog: Catalog) -> Reader:
    tools = catalog.read_tools()
    log.info("read tools offered: %s", [t.name for t in tools])
    fenced = FencedGateway(deps.gateway)
    return Reader(tools=tools, fenced=fenced, health=ToolHealth(fenced), source_of=catalog.source_of,
                  helpers=frozenset({RECOVER_META}))


@dataclass(frozen=True)
class SheetOutcome:
    """The table read, or a pick to ask, or a fixed reply that ends the turn."""

    table: tables.Table | None = None
    reply: str | None = None
    candidates: tuple[tuple[str, str], ...] = ()      # (id, title), verified against tool output
    off_topic: bool = False


async def read(deps: TurnDeps, reader: Reader, query: str, request: Request, sheet_id: str | None = None,
               title: str | None = None) -> SheetOutcome:
    if not reader.tools_for("spreadsheet"):
        return SheetOutcome(reply=prompts.NO_SHEET_TOOL)
    system = prompts.sheet_system(title or request.sheet, request.tab, sheet_id)
    result = await reader.loop(deps, "spreadsheet", ({"role": "system", "content": system},
                                                     {"role": "user", "content": query}), READ_LOOP_CALLS)
    status = reader.status("spreadsheet")
    answer = load_json_object(result.text) if result.status == "completed" else None
    if answer and answer.get("status") == "off_topic":
        return SheetOutcome(off_topic=True)
    rows = find_rows(reader.fenced.outputs)
    found = tables.from_rows(rows, f"sheet:{_title(answer, title, reader)}") if rows else None
    if found is not None:
        return SheetOutcome(table=found)
    if not status.ok:
        return SheetOutcome(reply=f"{coverage.line(status)} {prompts.PASTE_HINT}")
    if result.status in _PAUSED:
        return SheetOutcome(reply=prompts.READ_NEEDS_APPROVAL)
    candidates = _candidates(answer, reader.fenced.seen_text)
    if candidates and not sheet_id:
        return SheetOutcome(candidates=candidates)
    if answer and answer.get("status") == "not_found" and (title or request.sheet):
        return SheetOutcome(reply=f"I couldn't find a spreadsheet called \"{title or request.sheet}\". "
                                  f"{prompts.PASTE_HINT}")
    if not request.sheet and not sheet_id:
        return SheetOutcome(reply=prompts.NO_SHEET_NAMED)
    return SheetOutcome(reply=prompts.SHEET_UNREADABLE)


def find_rows(outputs: Sequence[str]) -> list[list[Any]]:
    """The biggest list of rows (lists of plain cells, two or more rows) any tool output carried; the latest
    wins a tie."""
    best: list[list[Any]] = []
    for raw in outputs:
        data = _json(raw)
        for rows in _row_lists(data):
            if sum(len(r) for r in rows) >= sum(len(r) for r in best):
                best = rows
    return best


def _row_lists(value: Any) -> list[list[list[Any]]]:
    if isinstance(value, Mapping):
        return [found for v in value.values() for found in _row_lists(v)]
    if isinstance(value, list):
        if len(value) >= 2 and all(isinstance(r, list) and all(_scalar(c) for c in r) for r in value):
            return [value]
        return [found for v in value for found in _row_lists(v)]
    if isinstance(value, str) and value.strip()[:1] in "[{":
        return _row_lists(_json(value))
    return []


def _scalar(cell: Any) -> bool:
    return cell is None or isinstance(cell, (str, int, float, bool))


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def load_json_object(text: str) -> dict | None:
    body = _CODE_FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    data = _json(body[start:end + 1])
    return data if isinstance(data, dict) else None


def appears_in(needle: Any, seen: str) -> bool:
    wanted = _WHITESPACE.sub(" ", str(needle or "")).strip().casefold()
    return bool(wanted) and wanted in _WHITESPACE.sub(" ", seen or "").casefold()


def _candidates(answer: dict | None, seen: str) -> tuple[tuple[str, str], ...]:
    raw = answer.get("candidates") if answer else None
    if not isinstance(raw, list):
        return ()
    found: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        cid, title = str(item.get("id") or "").strip(), str(item.get("title") or "").strip()
        if appears_in(cid, seen) and appears_in(title, seen) and all(t != title for _, t in found):
            found.append((cid, title))
    return tuple(found[:MAX_CANDIDATES])


def _title(answer: dict | None, title: str | None, reader: Reader) -> str:
    named = str((answer or {}).get("title") or "").strip()
    if named and appears_in(named, reader.fenced.seen_text):
        return named
    return title or "spreadsheet"
