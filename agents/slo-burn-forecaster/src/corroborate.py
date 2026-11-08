"""Optional, text-only corroboration for the top burners: error-tracker issues and incidents that may relate to
the worst window. One read-only tool loop per service (at most two services, two model calls each plus one
tool-less salvage call, ten tool calls per turn), fenced output, and every title and link checked verbatim against this turn's tool output. It is
never a cause and never feeds the burn maths. No connector -> one coverage line, no model call."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import coverage
import prompts
from burn import Forecast
from deps import TurnDeps
from discovery import Catalog, discover
from fence import FencedGateway
from models import RELATED_KINDS, Related
from nasiko_mcp import McpGatewayError, ToolOk, ToolResult
from render import clock, when
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("slo_burn.corroborate")

SOURCES = ("errors", "incidents")
MAX_SERVICES = 2
LOOP_ITERATIONS = 2  # plus one tool-less salvage call: at most 3 model calls per service
MAX_TOOL_CALLS = 10
MAX_ITEMS = 3
LLM_TIMEOUT_SECS = 45.0
SALVAGE = "Stop calling tools. Reply now with only the JSON object, using what the tool output already shows."
_JSON = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class Found:
    related: Mapping[str, tuple[Related, ...]]
    notes: tuple[str, ...]


class CallBudget:
    """Refuses tool calls past the turn's cap, so the model cannot spend the flow window on lookups."""

    def __init__(self, gateway: Any, cap: int = MAX_TOOL_CALLS):
        self._gateway = gateway
        self._left = cap

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self._left <= 0:
            note = json.dumps({"error": "The lookup budget for this turn is spent. Stop and answer now."})
            return ToolOk(content=({"type": "text", "text": note},), is_error=True, raw={})
        self._left -= 1
        return await self._gateway.call_tool(name, arguments, traceparent)


async def corroborate(deps: TurnDeps, targets: Sequence[Forecast]) -> Found:
    if not targets:
        return Found({}, ())
    catalog, failure = await discover(deps, ("read",))
    if failure is not None:
        return Found({}, (failure,))
    tools = catalog.read_tools()
    usable = [t for t in tools if catalog.source_of(t) in SOURCES]
    if not usable:
        return Found({}, (prompts.NO_RELATED_TOOLS,))
    fenced = FencedGateway(deps.gateway)
    health = ToolHealth(CallBudget(fenced))
    related: dict[str, tuple[Related, ...]] = {}
    for target in targets[:MAX_SERVICES]:
        items = await _lookup(deps, target, usable, health, fenced)
        if items:
            related[target.name] = items
    return Found(related, _notes(catalog, usable, health))


async def _lookup(deps: TurnDeps, target: Forecast, tools, health: ToolHealth, fenced: FencedGateway
                  ) -> tuple[Related, ...]:
    window = "unknown" if target.worst_window is None else \
        f"{when(target.worst_window[0])} to {clock(target.worst_window[1])}"
    messages = ({"role": "system", "content": prompts.corroborate_system(target.name, window)},
                {"role": "user", "content": prompts.fence_data(f"service: {target.name}")})
    try:
        result = await run_tool_loop(deps.llm, deps.model, messages, tools, health, deps.traceparent,
                                     max_iterations=LOOP_ITERATIONS, is_offered=health.is_offered,
                                     timeout=LLM_TIMEOUT_SECS)
        text = result.text
        if result.status == "max_iterations":
            salvage = await run_tool_loop(deps.llm, deps.model, (*result.messages, {"role": "user", "content": SALVAGE}),
                                          (), health, deps.traceparent, max_iterations=1, timeout=LLM_TIMEOUT_SECS)
            text = salvage.text
    except McpGatewayError as e:
        log.warning("corroboration for one service stopped: %s", type(e).__name__)
        return ()
    return verified(parse_items(text), fenced.seen_text)


def parse_items(text: str) -> list[Related]:
    match = _JSON.search(text or "")
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        return []
    raw = data.get("related") if isinstance(data, Mapping) else None
    items = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping) or entry.get("kind") not in RELATED_KINDS:
            continue
        title = str(entry.get("title") or "").strip()
        if title:
            items.append(Related(str(entry["kind"]), title, _opt(entry.get("url")), _opt(entry.get("when"))))
    return items


def _opt(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if text and text.casefold() != "null" else None


def verified(items: Sequence[Related], seen: str) -> tuple[Related, ...]:
    """Items whose title is verbatim in this turn's tool output; a link or time not in it is removed."""
    kept = []
    for item in items:
        if item.title not in seen:
            continue
        url = item.url if item.url and item.url.startswith("http") and item.url in seen else None
        moment = item.when if item.when and item.when in seen else None
        kept.append(Related(item.kind, item.title, url, moment))
    return tuple(kept[:MAX_ITEMS])


def _notes(catalog: Catalog, usable, health: ToolHealth) -> tuple[str, ...]:
    notes = []
    for source in SOURCES:
        status = coverage.assess(source, usable, catalog.source_of, health.dropped)
        if not status.ok:
            notes.append(coverage.line(status))
    return tuple(notes)
