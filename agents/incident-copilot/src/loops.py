"""One bounded read loop: its own discovery, its own ToolHealth, an excerpting fence that keeps the raw
output for checks, and step-limit salvage (one tool-less repair call, counted inside the loop's cap)."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import prompts
from deps import TurnDeps
from discovery import RECOVER_META, Catalog, discover
from evidence import Haystack
from excerpt import DEFAULT_CAP, ExcerptingGateway, excerpt
from fence import FencedGateway, RawCall
from nasiko_mcp import McpTool
from records import answer_list
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("incident_copilot.loops")

REPAIR_EVIDENCE_CHARS = 24_000  # the excerpts the model already saw, never the raw output


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


@dataclass
class Reader:
    """One loop's read side. Mutable on purpose: a loop cut off by its deadline is salvaged from what
    the fence recorded so far."""

    step: str
    tools: tuple[McpTool, ...] = ()
    fenced: FencedGateway | None = None
    health: ToolHealth | None = None
    failure: str | None = None
    cap: int = DEFAULT_CAP
    texts: list[str] = field(default_factory=list)

    @property
    def calls(self) -> tuple[RawCall, ...]:
        return self.fenced.calls if self.fenced else ()

    @property
    def offered_names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.tools if t.name != RECOVER_META)

    @property
    def dropped(self) -> Mapping[str, str]:
        return self.health.dropped if self.health else {}

    def haystack(self) -> Haystack:
        return Haystack(call.text for call in self.calls)


async def open_reader(deps: TurnDeps, step: str, cap: int = DEFAULT_CAP) -> Reader:
    catalog, failure = await discover(deps, step)
    if catalog is None:
        return Reader(step, failure=failure)
    return reader_for(deps, catalog, cap)


def reader_for(deps: TurnDeps, catalog: Catalog, cap: int = DEFAULT_CAP) -> Reader:
    tools = catalog.read_tools()
    if tools and catalog.recover is not None:
        tools = (*tools, catalog.recover)
    fenced = ExcerptingGateway(deps.gateway, cap)
    return Reader(catalog.step, tools, fenced, ToolHealth(fenced), cap=cap)


async def run(deps: TurnDeps, reader: Reader, system: str, request: str, max_calls: int,
              json_key: str | None = None) -> LoopResult:
    """The loop with `max_calls - 1` model calls, then (when `json_key` is given and the answer is not that
    JSON) one tool-less repair call over this loop's own tool output."""
    messages = ({"role": "system", "content": system}, {"role": "user", "content": request})
    budget = max(max_calls - 1, 1) if json_key else max_calls
    result = await run_tool_loop(deps.llm, deps.model, messages, reader.tools, reader.health, deps.traceparent,
                                 max_iterations=budget, is_offered=reader.health.is_offered)
    reader.texts.append(result.text)
    if json_key is None or result.status in ("approval_pending", "authorization_required"):
        return result
    if result.status == "completed" and answer_list(result.text, json_key) is not None:
        return result
    if max_calls < 2:
        return result
    log.info("step %s ended %s without its JSON; one repair call", reader.step, result.status)
    shown = "\n".join(excerpt(call.text, reader.cap) for call in reader.calls)[:REPAIR_EVIDENCE_CHARS]
    evidence = prompts.fence(shown) if shown else "(no tool results in this step)"
    repair = await ask_once(deps, (
        {"role": "system", "content": system}, {"role": "user", "content": request},
        {"role": "user", "content": f"Tool results from this step:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    ))
    reader.texts.append(repair)
    return LoopResult("completed", repair, result.messages, None, result.tools_called)
