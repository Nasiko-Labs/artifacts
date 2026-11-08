"""The model-facing steps, each bounded and post-checked in code: the scope check, the balance-transaction read
(paged by week), the paid-invoice read, the one tool-less narrative call, and the follow-up answer. Also the shared
read side (`Reader`) and `ask_once`."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import checks
import coverage
import narrative
import prompts
import rows as r
from deps import LLM_CALL_TIMEOUT_SECS, TurnDeps
from discovery import RECOVER_META, Catalog
from fence import FencedGateway
from nasiko_mcp import McpTool
from render import Report
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("finance_rollups.steps")

BALANCE_LOOP_CALLS = 8       # plus one JSON repair
INVOICES_LOOP_CALLS = 6
BOUNDARY_SECS = int(timedelta(days=2).total_seconds())
_WEEK_SECS = int(timedelta(days=7).total_seconds())
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


async def in_scope(deps: TurnDeps, query: str) -> bool:
    text = await ask_once(deps, ({"role": "system", "content": prompts.SCOPE_CHECK_SYSTEM},
                                 {"role": "user", "content": query}))
    return not text.upper().startswith(prompts.OFF_TOPIC)


@dataclass(frozen=True)
class Reader:
    """One turn's read side: the payments read tools, the fence adapter that records tool output, and the failure
    tracker in front of it."""

    tools: tuple[McpTool, ...]
    fenced: FencedGateway
    health: ToolHealth

    @property
    def own(self) -> tuple[McpTool, ...]:
        return tuple(t for t in self.tools if t.name != RECOVER_META)

    def status(self) -> coverage.SourceStatus:
        return coverage.assess(self.own, self.health.dropped)

    async def loop(self, deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_iterations: int) -> LoopResult:
        return await run_tool_loop(deps.llm, deps.model, messages, self.tools, self.health, deps.traceparent,
                                   max_iterations=max_iterations, is_offered=self.health.is_offered,
                                   timeout=LLM_CALL_TIMEOUT_SECS)


def reader_for(deps: TurnDeps, catalog: Catalog) -> Reader:
    own = catalog.for_source("payments")
    tools = (*own, catalog.recover) if own and catalog.recover else own
    log.info("payments read tools offered: %s", [t.name for t in tools])
    fenced = FencedGateway(deps.gateway)
    return Reader(tools=tools, fenced=fenced, health=ToolHealth(fenced))


def weeks(start: int, end: int) -> tuple[tuple[int, int], ...]:
    out: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        out.append((cursor, min(cursor + _WEEK_SECS, end)))
        cursor += _WEEK_SECS
    return tuple(out)


# --- balance transactions -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class BalanceOutcome:
    """Either a fixed reply that ends the turn (`reply`, or `off_topic`), or the checked rows."""

    reply: str | None = None
    checked: r.Checked | None = None
    has_more: bool = False
    livemode: bool | None = None
    off_topic: bool = False


async def fetch_balance(deps: TurnDeps, reader: Reader, query: str, start: int, end: int) -> BalanceOutcome:
    if not reader.own:
        return BalanceOutcome(prompts.NO_PAYMENTS_TOOL)
    system = prompts.balance_system(start, end, weeks(start, end))
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await reader.loop(deps, messages, BALANCE_LOOP_CALLS)
    status = reader.status()
    if not status.ok:
        return BalanceOutcome(f"{prompts.NO_PAYMENTS_TOOL}\n\n{coverage.line(status)}")
    if result.status in _PAUSED:
        return BalanceOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    parsed = None if hit_limit else r.parse_answer(result.text, "rows")
    if parsed is None and reader.fenced.seen_text:
        parsed = await _repair(deps, system, query, result, reader.fenced.seen_text)
    if parsed is None:
        return BalanceOutcome(prompts.STEP_LIMIT_NOTE if hit_limit else prompts.UNREADABLE_ANSWER)
    if parsed.status == "off_topic":
        return BalanceOutcome(off_topic=True)
    if parsed.status == "no_read_tool":
        return BalanceOutcome(prompts.NO_PAYMENTS_TOOL)
    checked = r.check_balance(parsed.raw, reader.fenced.seen_text, start, end)
    if checked.dropped:
        log.info("post-check dropped %d balance row(s) not backed by tool output", checked.dropped)
    return BalanceOutcome(checked=checked, has_more=parsed.has_more or hit_limit,
                          livemode=checks.livemode(reader.fenced.seen_text))


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult, seen_text: str) -> r.Answer | None:
    log.info("balance answer was not the agreed JSON; asking once for a rewrite")
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this turn:\n{prompts.fence(seen_text)}\n\nYour previous "
                                    f"answer:\n{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return r.parse_answer(await ask_once(deps, messages), "rows")


# --- invoices -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class InvoicesOutcome:
    invoices: tuple[r.InvoiceRow, ...] | None    # None: not read
    dropped: int = 0
    has_more: bool = False


async def fetch_invoices(deps: TurnDeps, reader: Reader, start: int, end: int) -> InvoicesOutcome:
    """Paid invoices from two days before the range to two days after it (the margins feed the timing check)."""
    lo, hi = start - BOUNDARY_SECS, end + BOUNDARY_SECS
    if not any(reader.health.is_offered(t.name) for t in reader.own):
        return InvoicesOutcome(None)
    messages = ({"role": "system", "content": prompts.invoices_system(lo, hi)},
                {"role": "user", "content": "List the paid invoices."})
    seen_before, dropped_before = len(reader.fenced.seen_text), set(reader.health.dropped)
    result = await reader.loop(deps, messages, INVOICES_LOOP_CALLS)
    parsed = r.parse_answer(result.text, "invoices") if result.status == "completed" else None
    failed = bool(set(reader.health.dropped) - dropped_before) and not (parsed and parsed.raw)
    if parsed is None or failed or len(reader.fenced.seen_text) == seen_before:
        log.info("invoice step ended %s without a usable answer; cash view only", result.status)
        return InvoicesOutcome(None)
    checked = r.check_invoices(parsed.raw, reader.fenced.seen_text, lo, hi)
    return InvoicesOutcome(checked.rows, checked.dropped, parsed.has_more)


# --- narrative and follow-ups ---------------------------------------------------------------------------------

async def write_narrative(deps: TurnDeps, report: Report) -> tuple[str, bool]:
    """(narrative, whether the model's text passed the code check); the template otherwise."""
    if not report.rollups:
        return "", True
    messages = ({"role": "system", "content": prompts.NARRATIVE_SYSTEM},
                {"role": "user", "content": prompts.fence_prior(narrative.facts_json(report))})
    try:
        text = (await ask_once(deps, messages)).strip().strip('"')
    except Exception:  # the figures still stand without a model narrative
        log.exception("narrative call failed; using the template")
        return narrative.template(report), False
    problem = narrative.problem(text, report)
    if problem:
        log.info("narrative rejected (%s); using the template", problem)
        return narrative.template(report), False
    return text, True


async def answer_followup(deps: TurnDeps, shown: str, query: str) -> str:
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(shown)}"
    return await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
