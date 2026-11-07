"""The read steps: invoices, customer and reminders, each a bounded tool loop whose JSON answer is checked
in code. Also the per-source read side (`Reader`) and the tool-less single call (`ask_once`)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import checks
import coverage
import invoices as inv
import prompts
import smalltalk
from deps import TurnDeps
from discovery import Catalog, classify
from fence import FencedGateway
from nasiko_mcp import McpTool
from send_guard import name_tokens
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("invoice_chaser.steps")

INVOICES_LOOP_CALLS = 5   # plus one JSON repair
CUSTOMER_LOOP_CALLS = 4
REMINDERS_LOOP_CALLS = 6
_PAUSED = ("approval_pending", "authorization_required")
# File-store and memory tools come back from every search; whatever their description says, they read
# neither invoices nor mail.
_NOT_SOURCE_WORDS = frozenset({"file", "files", "folder", "folders", "upload", "download", "memory"})


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
    """One source's read side for this turn: the tools offered to the model, the fence adapter that
    records their output, and the failure tracker in front of it."""

    source: str
    tools: tuple[McpTool, ...]
    fenced: FencedGateway
    health: ToolHealth
    helpers: frozenset[str] = frozenset()

    async def loop(self, deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_iterations: int) -> LoopResult:
        return await run_tool_loop(deps.llm, deps.model, messages, self.tools, self.health, deps.traceparent,
                                   max_iterations=max_iterations, is_offered=self.health.is_offered)

    @property
    def status(self) -> coverage.SourceStatus:
        return coverage.assess(self.source, self.tools, self.health.dropped, self.helpers)

    @property
    def seen_text(self) -> str:
        return self.fenced.seen_text


def reader_for(deps: TurnDeps, catalog: Catalog, source: str) -> Reader:
    """The read tools whose own name or description places them in this source, plus the recovery
    helper. A search hit for this source's query is not enough: unrelated tools (file stores, memory
    search) come back from every query and would only burn the step's calls."""
    own = tuple(t for t in catalog.read_tools()
                if t.name != (catalog.recover.name if catalog.recover else None) and classify(t) == source
                and not _NOT_SOURCE_WORDS & set(name_tokens(t.name)))
    tools = (*own, catalog.recover) if own and catalog.recover else own
    fenced = FencedGateway(deps.gateway)
    helpers = frozenset({catalog.recover.name}) if catalog.recover else frozenset()
    return Reader(source, tools, fenced, ToolHealth(fenced), helpers)


# --- invoices -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class InvoicesOutcome:
    """Either a fixed reply that ends the turn, or the checked invoices."""

    reply: str | None
    invoices: tuple[inv.Invoice, ...] = ()
    livemode: bool | None = None
    dropped: int = 0
    no_due_date: int = 0
    salvaged: bool = False


async def read_invoices(deps: TurnDeps, reader: Reader, query: str) -> InvoicesOutcome:
    system = prompts.invoices_system(deps.now)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await reader.loop(deps, messages, INVOICES_LOOP_CALLS)
    if not reader.status.ok:
        return InvoicesOutcome(coverage.payments_failed_reply(reader.status))
    if result.status in _PAUSED:
        return InvoicesOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    parsed = None if hit_limit else inv.parse_invoices(result.text)
    if parsed is None and (reader.seen_text or not hit_limit):
        parsed = await _repair(deps, system, query, result.text, reader.seen_text, inv.parse_invoices)
    if parsed is None:
        return InvoicesOutcome(prompts.STEP_LIMIT_NOTHING if hit_limit else prompts.UNREADABLE_ANSWER)
    if parsed.status == "off_topic":
        return InvoicesOutcome(smalltalk.OFF_TOPIC_REPLY)
    if parsed.status == "no_tool":
        return InvoicesOutcome(prompts.NO_PAYMENTS_TOOL)
    checked = checks.post_check_invoices(parsed.invoices, today=deps.today, seen_text=reader.seen_text)
    if checked.dropped:
        log.info("post-check dropped %d invoice(s) (unbacked, not open, not yet due, or no amount)", checked.dropped)
    livemode = parsed.livemode if "livemode" in reader.seen_text else None
    return InvoicesOutcome(None, checked.invoices, livemode, checked.dropped, checked.no_due_date, hit_limit)


async def _repair(deps: TurnDeps, system: str, query: str, previous: str, seen_text: str, parse):
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(previous)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return parse(await ask_once(deps, messages))


# --- customer -------------------------------------------------------------------------------------

def needs_customer(invoices: Sequence[inv.Invoice]) -> bool:
    return any(i.customer_id and not (i.customer_email and i.customer_name) for i in invoices)


async def fill_customers(deps: TurnDeps, reader: Reader, invoices: Sequence[inv.Invoice]) -> tuple[inv.Invoice, ...]:
    wanted = list(dict.fromkeys(i.customer_id for i in invoices
                                if i.customer_id and not (i.customer_email and i.customer_name)))
    messages = ({"role": "system", "content": prompts.CUSTOMER_SYSTEM},
                {"role": "user", "content": prompts.fence_candidates(json.dumps(wanted))})
    result = await reader.loop(deps, messages, CUSTOMER_LOOP_CALLS)
    found = inv.parse_customers(result.text) if result.status == "completed" else {}
    return checks.check_customers(invoices, found, reader.seen_text)


# --- reminders ------------------------------------------------------------------------------------

async def read_reminders(deps: TurnDeps, reader: Reader, entries: Sequence[inv.Entry]) -> tuple[inv.Entry, ...] | None:
    """Entries with verified history, or None when the step could not complete (history unchecked)."""
    listing = json.dumps([_candidate(e.invoice) for e in entries], ensure_ascii=False, indent=1)
    messages = ({"role": "system", "content": prompts.reminders_system(deps.now)},
                {"role": "user", "content": prompts.fence_candidates(listing)})
    result = await reader.loop(deps, messages, REMINDERS_LOOP_CALLS)
    parsed = inv.parse_reminders(result.text) if result.status == "completed" else None
    if parsed is None or not reader.status.ok:
        log.info("reminders step ended %s; history stays unchecked", result.status)
        return None
    return checks.attach_history(entries, parsed.reminders, parsed.replies, now=deps.now, seen_text=reader.seen_text)


def _candidate(i: inv.Invoice) -> dict:
    since = i.due_day - timedelta(days=checks.REMINDER_LOOKBACK_DAYS)
    return {"invoice_id": i.id, "number": i.number, "customer_email": i.customer_email, "hosted_url": i.hosted_url,
            "due_date": i.due, "search_from": since.isoformat(), "search_from_slashed": since.strftime("%Y/%m/%d")}
