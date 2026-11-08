"""The read steps, each a bounded tool loop post-checked in code: subscriptions (paged, <= 8 calls + 1 repair), events
(plan changes and failed payments, <= 6), customer names (<= 3), chat channels (<= 3), and the one tool-less headline
call. Also the shared read side (`Reader`) and `ask_once`."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import checks
import coverage
import post
import prompts
import subs
from deps import LLM_CALL_TIMEOUT_SECS, TurnDeps
from discovery import RECOVER_META, Catalog
from fence import FencedGateway
from nasiko_mcp import McpTool
from report import Report
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("revenue_anomaly.steps")

SUBS_LOOP_CALLS = 8      # plus one JSON repair
EVENTS_LOOP_CALLS = 6
CUSTOMERS_LOOP_CALLS = 3
CHANNELS_LOOP_CALLS = 3
MAX_NAMED_CUSTOMERS = 10
MAX_CHANNELS = 10
_PAUSED = ("approval_pending", "authorization_required")


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1,
                                 timeout=LLM_CALL_TIMEOUT_SECS)
    return result.text.strip()


@dataclass(frozen=True)
class Reader:
    """One turn's read side: every read tool, the fence adapter that records tool output, and the failure tracker in
    front of it. Each step is offered only its own source's tools."""

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
                                   timeout=LLM_CALL_TIMEOUT_SECS)


def reader_for(deps: TurnDeps, catalog: Catalog) -> Reader:
    tools = catalog.read_tools()
    log.info("read tools offered: %s", [t.name for t in tools])
    fenced = FencedGateway(deps.gateway)
    return Reader(tools=tools, fenced=fenced, health=ToolHealth(fenced), source_of=catalog.source_of,
                  helpers=frozenset({RECOVER_META}))


# --- subscriptions --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SubsOutcome:
    """Either a fixed reply that ends the turn, or the checked subscriptions."""

    reply: str | None
    subs: tuple[subs.Sub, ...] = ()
    dropped: int = 0
    pages_left_out: bool = False
    livemode: bool | None = None


async def fetch_subscriptions(deps: TurnDeps, reader: Reader, query: str, since: datetime) -> SubsOutcome:
    if not reader.tools_for("payments"):
        return SubsOutcome(prompts.NO_PAYMENTS_TOOL)
    system = prompts.subs_system(deps.now, since)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await reader.loop(deps, "payments", messages, SUBS_LOOP_CALLS)
    status = reader.status("payments")
    if not status.ok:
        return SubsOutcome(f"{coverage.line(status)}\n\n{prompts.NO_PAYMENTS_TOOL}")
    if result.status in _PAUSED:
        return SubsOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    parsed = None if hit_limit else subs.parse_subs_answer(result.text)
    if parsed is None and reader.fenced.seen_text:
        parsed = await _repair(deps, system, query, result, reader.fenced.seen_text)
    if parsed is None:
        return SubsOutcome(prompts.STEP_LIMIT_NOTE if hit_limit else prompts.UNREADABLE_ANSWER)
    if parsed.status == "off_topic":
        return SubsOutcome(prompts.OFF_TOPIC)
    if parsed.status == "no_read_tool":
        return SubsOutcome(prompts.NO_PAYMENTS_TOOL)
    kept, dropped = subs.check_subs(parsed.raw, reader.fenced.seen_text)
    if dropped:
        log.info("post-check dropped %d subscription(s) not backed by tool output", dropped)
    if not kept:
        return SubsOutcome(_dropped_line(reader) or prompts.NO_SUBSCRIPTIONS)
    return SubsOutcome(None, kept, dropped, parsed.has_more or hit_limit, checks.livemode(reader.fenced.seen_text))


def _dropped_line(reader: Reader) -> str | None:
    """When nothing was read and a payments tool was dropped as unavailable: say so, with its reason."""
    reasons = [r for name, r in reader.health.dropped.items()
               if any(t.name == name for t in reader.tools_for("payments"))]
    if not reasons:
        return None
    status = coverage.SourceStatus("payments", "unavailable", ", ".join(dict.fromkeys(reasons)))
    return f"{coverage.line(status)}\n\n{prompts.NO_PAYMENTS_TOOL}"


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult,
                  seen_text: str) -> subs.SubsAnswer | None:
    log.info("subscriptions answer was not the agreed JSON; asking once for a rewrite")
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this turn:\n{prompts.fence(seen_text)}\n\nYour previous "
                                    f"answer:\n{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return subs.parse_subs_answer(await ask_once(deps, messages))


# --- events -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class EventsOutcome:
    changes: tuple[subs.Change, ...] = ()
    failures: tuple[subs.Failure, ...] = ()
    read: bool = False


async def fetch_events(deps: TurnDeps, reader: Reader, known: Sequence[subs.Sub], since: datetime) -> EventsOutcome:
    """Plan changes and failed payments since `since`; `read` is False when they could not be read to the end."""
    messages = ({"role": "system", "content": prompts.events_system(deps.now, since)},
                {"role": "user", "content": "List the plan changes and the failed payments."})
    result = await reader.loop(deps, "payments", messages, EVENTS_LOOP_CALLS)
    if result.status != "completed":
        log.info("events step ended %s; expansion/contraction not computed", result.status)
        return EventsOutcome()
    changes = subs.list_of(result.text, "changes")
    failures = subs.list_of(result.text, "failures")
    if changes is None and failures is None:
        return EventsOutcome()
    seen = reader.fenced.seen_text
    kept_failures, _ = subs.check_failures(failures or (), seen)
    return EventsOutcome(subs.check_changes(changes or (), seen, {s.id for s in known}), kept_failures,
                         changes is not None and failures is not None)


# --- customers and channels -------------------------------------------------------------------------------------

async def fetch_customers(deps: TurnDeps, reader: Reader, ids: set[str]) -> dict[str, subs.Customer]:
    """Names (or emails) for the customers that will be named; ids stand in for any not found."""
    wanted = sorted(ids)[:MAX_NAMED_CUSTOMERS]
    if not wanted or not reader.tools_for("payments"):
        return {}
    messages = ({"role": "system", "content": prompts.customers_system(wanted)},
                {"role": "user", "content": "Look up these customers."})
    result = await reader.loop(deps, "payments", messages, CUSTOMERS_LOOP_CALLS)
    raw = subs.list_of(result.text, "customers") if result.status == "completed" else None
    return subs.check_customers(raw or (), reader.fenced.seen_text, set(wanted))


async def find_channels(deps: TurnDeps, reader: Reader, name: str | None) -> tuple[subs.Channel, ...]:
    if not reader.tools_for("chat"):
        return ()
    messages = ({"role": "system", "content": prompts.channels_system(name)},
                {"role": "user", "content": f"Find #{name}." if name else "List the channels."})
    result = await reader.loop(deps, "chat", messages, CHANNELS_LOOP_CALLS)
    raw = subs.list_of(result.text, "channels") if result.status == "completed" else None
    return subs.check_channels(raw or (), reader.fenced.seen_text)[:MAX_CHANNELS]


# --- the headline -------------------------------------------------------------------------------------------------

async def write_headline(deps: TurnDeps, report: Report) -> tuple[str, bool]:
    """(headline, whether the model's sentence passed the code check); the code headline otherwise."""
    facts = json.dumps(post.facts(report), ensure_ascii=False)
    messages = ({"role": "system", "content": prompts.headline_system()},
                {"role": "user", "content": prompts.fence_prior(facts)})
    try:
        text = (await ask_once(deps, messages)).strip().strip('"')
    except Exception:  # the check still stands without a model headline
        log.exception("headline call failed; using the code headline")
        return post.code_headline(report), False
    problem = post.headline_problem(text, report)
    if problem:
        log.info("headline rejected (%s); using the code headline", problem)
        return post.code_headline(report), False
    return text, True
