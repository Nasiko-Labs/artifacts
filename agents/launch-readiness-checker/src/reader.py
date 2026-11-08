"""One source's read side for a turn, and the shared step runner: a bounded tool loop over that source's read
tools, its JSON answer repaired once if needed, then checked in code against this step's tool output.
Also the tool-less single call (`ask_once`)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import coverage
import prompts
from budget import BudgetExhausted, CappedCaller
from config import STEP_DEADLINE_SECS, STEP_LOOP_CALLS, STEP_TOOL_CALLS
from deps import TurnDeps
from discovery import Catalog, Source
from fence import FencedGateway, Record
from nasiko_mcp import McpTool
from records import Parsed, Seen, json_object
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("launch_readiness.reader")
_PAUSED = ("approval_pending", "authorization_required")


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


@dataclass(frozen=True)
class Reader:
    source: str
    tools: tuple[McpTool, ...]
    fenced: FencedGateway
    health: ToolHealth
    capped: CappedCaller
    helpers: frozenset[str] = frozenset()

    @property
    def status(self) -> coverage.SourceStatus:
        return coverage.assess(self.source, self.tools, self.health.dropped, self.helpers)

    @property
    def seen(self) -> Seen:
        return Seen(seen_text(self.fenced.records))


def reader_for(deps: TurnDeps, catalog: Catalog | None, source: Source) -> Reader:
    """The read tools whose own name or description places them in this source, plus the recovery helper."""
    own = catalog.for_source(source) if catalog else ()
    recover = catalog.recover if catalog else None
    tools = (*own, recover) if own and recover else own
    fenced = FencedGateway(deps.gateway)
    health = ToolHealth(fenced)
    helpers = frozenset({recover.name}) if recover else frozenset()
    return Reader(source, tuple(tools), fenced, health, CappedCaller(health, STEP_TOOL_CALLS), helpers)


def seen_text(records: Sequence[Record]) -> str:
    """Raw bodies plus their JSON strings unescaped, so a title with quotes or accents still matches."""
    return "\n".join(part for r in records for part in (r.body, *flat_strings(r.body)))


def flat_strings(body: str) -> list[str]:
    try:
        data = json.loads(body)
    except ValueError:
        return []
    return _strings(data)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    return []


@dataclass(frozen=True)
class StepOutcome:
    source: str
    status: coverage.SourceStatus
    parsed: Parsed = field(default_factory=Parsed)
    salvaged: bool = False
    records: tuple[Record, ...] = ()


async def run_step(deps: TurnDeps, reader: Reader, system: str, user: str,
                   parse: Callable[[Mapping[str, Any], Seen], Parsed]) -> StepOutcome:
    """The step within its own deadline; a timeout or a spent budget salvages what was read."""
    try:
        return await asyncio.wait_for(_step(deps, reader, system, user, parse), STEP_DEADLINE_SECS)
    except TimeoutError:
        log.warning("%s step passed its %ss deadline", reader.source, STEP_DEADLINE_SECS)
        return _salvage(reader, "timed out")


async def _step(deps: TurnDeps, reader: Reader, system: str, user: str,
                parse: Callable[[Mapping[str, Any], Seen], Parsed]) -> StepOutcome:
    messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
    try:
        result = await run_tool_loop(deps.llm, deps.model, messages, reader.tools, reader.capped, deps.traceparent,
                                     max_iterations=STEP_LOOP_CALLS, is_offered=reader.health.is_offered)
    except BudgetExhausted:
        return _salvage(reader, "call budget spent")
    if not reader.status.ok:
        return StepOutcome(reader.source, reader.status)
    if result.status in _PAUSED:
        return StepOutcome(reader.source, coverage.SourceStatus(reader.source, "unavailable", "read needs approval"))
    hit_limit = result.status == "max_iterations"
    data = None if hit_limit else json_object(result.text)
    if data is None and reader.fenced.records:
        data = await _repair(deps, system, user, result.text, reader)
    if data is None:
        return _salvage(reader, "no usable answer from the model" if reader.fenced.records else "nothing was read")
    return StepOutcome(reader.source, reader.status, parse(data, reader.seen), hit_limit or reader.capped.hit,
                       reader.fenced.records)


async def _repair(deps: TurnDeps, system: str, user: str, previous: str, reader: Reader) -> Mapping | None:
    log.info("%s answer was not the agreed JSON; asking once for a rewrite", reader.source)
    evidence = prompts.fence(reader.fenced.seen_text)
    try:
        text = await ask_once(deps, (
            {"role": "system", "content": system}, {"role": "user", "content": user},
            {"role": "user", "content": f"Tool results from this step:\n{evidence}\n\nYour previous answer:\n"
                                        f"{prompts.fence(previous)}\n\n{prompts.REPAIR_INSTRUCTION}"}))
    except BudgetExhausted:
        return None
    return json_object(text)


def _salvage(reader: Reader, reason: str) -> StepOutcome:
    """No checked answer: the area is not read (its rules are `unknown`), never passed on partial evidence."""
    status = coverage.SourceStatus(reader.source, "unavailable", reason)
    return StepOutcome(reader.source, status, salvaged=bool(reader.fenced.records), records=reader.fenced.records)
