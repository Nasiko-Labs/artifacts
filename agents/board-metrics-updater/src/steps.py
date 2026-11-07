"""The read steps, each a bounded tool loop post-checked in code: subscriptions (paged), plan changes
(last 30 days), the spreadsheet/doc list for a pick, opening one sheet (its header) or doc, and the one
tool-less narrative call. Also the shared read side (`Reader`) and `ask_once`."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import checks
import coverage
import narrative
import prompts
import subs
from deps import TurnDeps
from discovery import RECOVER_META, Catalog
from fence import FencedGateway
from mrr import tone
from nasiko_mcp import McpTool
from render import Report
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("board_metrics.steps")

SUBS_LOOP_CALLS = 8      # plus one JSON repair
CHANGES_LOOP_CALLS = 4
TARGETS_LOOP_CALLS = 3
OPEN_LOOP_CALLS = 4
LLM_TIMEOUT_SECS = 45.0
CHANGES_TIME_BUDGET_SECS = 270
MAX_TARGETS = 10
_PAUSED = ("approval_pending", "authorization_required")


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1,
                                 timeout=LLM_TIMEOUT_SECS)
    return result.text.strip()


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


# --- subscriptions --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SubsOutcome:
    """Either a fixed reply that ends the turn, or the checked subscriptions."""

    reply: str | None
    subs: tuple[subs.Sub, ...] = ()
    dropped: int = 0
    pages_left_out: bool = False
    livemode: bool | None = None


async def fetch_subscriptions(deps: TurnDeps, reader: Reader, query: str, t0: datetime,
                              t1: datetime) -> SubsOutcome:
    if not reader.tools_for("payments"):
        return SubsOutcome(prompts.NO_PAYMENTS_TOOL)
    system = prompts.subs_system(deps.now, t0, t1)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await reader.loop(deps, "payments", messages, SUBS_LOOP_CALLS)
    status = reader.status("payments")
    if not status.ok:
        return SubsOutcome(f"{prompts.NO_PAYMENTS_TOOL}\n\n{coverage.line(status)}")
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
        return SubsOutcome(prompts.NO_SUBSCRIPTIONS)
    return SubsOutcome(None, kept, dropped, parsed.has_more or hit_limit, checks.livemode(reader.fenced.seen_text))


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


# --- plan changes -----------------------------------------------------------------------------------------

async def fetch_changes(deps: TurnDeps, reader: Reader, known: Sequence[subs.Sub]) -> tuple[subs.Change, ...] | None:
    """Plan changes of the last 30 days, or None when they could not be read to the end."""
    messages = ({"role": "system", "content": prompts.changes_system(deps.now)},
                {"role": "user", "content": "List the plan changes."})
    result = await reader.loop(deps, "payments", messages, CHANGES_LOOP_CALLS)
    raw = subs.parse_changes_answer(result.text) if result.status == "completed" else None
    if raw is None:
        log.info("plan-change step ended %s; expansion/contraction not computed", result.status)
        return None
    return subs.check_changes(raw, reader.fenced.seen_text, {s.id for s in known})


# --- sheets and docs --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Target:
    id: str
    name: str
    tab: str = ""
    header: tuple[str, ...] = ()


async def list_targets(deps: TurnDeps, reader: Reader, kind: str) -> tuple[Target, ...]:
    """Spreadsheets or documents a tool returned this turn (verbatim), at most ten."""
    source = "spreadsheet" if kind == "sheet" else "doc"
    if not reader.tools_for(source):
        return ()
    messages = ({"role": "system", "content": prompts.targets_system(kind)},
                {"role": "user", "content": "List them."})
    result = await reader.loop(deps, source, messages, TARGETS_LOOP_CALLS)
    data = checks.load_json_object(result.text) if result.status == "completed" else None
    items = data.get("items") if data else None
    if not isinstance(items, list):
        return ()
    seen = checks.normalise_text(reader.fenced.seen_text)
    found: tuple[Target, ...] = ()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        tid, name = checks.text_of(item.get("id")), checks.text_of(item.get("name"))
        if checks.appears_in(tid, seen) and checks.appears_in(name, seen) and all(t.name != name for t in found):
            found = (*found, Target(tid, name))
    return found[:MAX_TARGETS]


async def open_target(deps: TurnDeps, reader: Reader, kind: str, name: str) -> Target | None:
    """The named sheet (with its header row) or doc, checked against tool output; None when not found."""
    source = "spreadsheet" if kind == "sheet" else "doc"
    system = prompts.open_sheet_system(name) if kind == "sheet" else prompts.open_doc_system(name)
    result = await reader.loop(deps, source, ({"role": "system", "content": system},
                                              {"role": "user", "content": f"Open {name}."}), OPEN_LOOP_CALLS)
    data = checks.load_json_object(result.text) if result.status == "completed" else None
    if not data or data.get("found") is False:
        return None
    seen = checks.normalise_text(reader.fenced.seen_text)
    tid, found_name = checks.text_of(data.get("id")), checks.text_of(data.get("name"))
    if not (checks.appears_in(tid, seen) and checks.appears_in(found_name, seen)):
        return None
    header = data.get("header") if isinstance(data.get("header"), list) else []
    cells = tuple(checks.text_of(h) for h in header)
    if not all(checks.appears_in(c, seen) for c in cells if c):
        cells = ()  # a header the tools never returned is not used: the default order is asked for
    tab = checks.text_of(data.get("tab"))
    return Target(tid, found_name, tab if checks.appears_in(tab, seen) else "", cells)


# --- narrative ---------------------------------------------------------------------------------------------

async def write_narrative(deps: TurnDeps, report: Report) -> tuple[str, bool]:
    """(paragraph, whether the model's paragraph passed the code check); the template otherwise."""
    if not report.blocks:
        return "", True
    facts = "\n".join(narrative.facts_json(b, report) for b in report.blocks)
    messages = ({"role": "system", "content": prompts.narrative_system(tone(report.blocks[0]))},
                {"role": "user", "content": prompts.fence_prior(facts)})
    try:
        text = (await ask_once(deps, messages)).strip().strip('"')
    except Exception:  # the figures still stand without a model paragraph
        log.exception("narrative call failed; using the template")
        return narrative.template(report), False
    problem = narrative.problem(text, report)
    if problem:
        log.info("narrative rejected (%s); using the template", problem)
        return narrative.template(report), False
    return text, True


def listing(targets: Sequence[Target]) -> str:
    return json.dumps([{"id": t.id, "name": t.name} for t in targets], ensure_ascii=False)
