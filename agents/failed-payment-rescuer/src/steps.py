"""The read steps of turn 1, each a bounded tool loop post-checked in code: failures (payments), enrich
(payment history per customer), contact (sent email in the last three days), and the channel list for
the digest question. Also the shared read side (`Reader`) and the tool-less single call (`ask_once`)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import checks
import coverage
import failures as fl
import prompts
import scoring
import smalltalk
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("payment_rescuer.steps")

FAILURES_LOOP_CALLS = 5   # plus one JSON repair
ENRICH_LOOP_CALLS = 6
CONTACT_LOOP_CALLS = 4
CHANNELS_LOOP_CALLS = 2
LLM_TIMEOUT_SECS = 45.0
CONTACT_TIME_BUDGET_SECS = 240  # enrich must end by then for the contact step to run
MAX_CHANNEL_OPTIONS = 10
_PAUSED = ("approval_pending", "authorization_required")
_NOTHING_CHECKED = checks.Checked(())


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
    """One turn's read side: every read tool, the fence adapter that records tool output, and the
    failure tracker in front of it. Each step is offered only its own source's tools."""

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


# --- failures -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FailuresOutcome:
    """Either a fixed reply that ends the turn, or the checked failures for the next steps."""

    reply: str | None
    checked: checks.Checked = _NOTHING_CHECKED
    livemode: bool | None = None
    notes: tuple[str, ...] = ()
    salvaged: bool = False


async def fetch_failures(deps: TurnDeps, reader: Reader, query: str, request: fl.Request) -> FailuresOutcome:
    if not reader.tools_for("payments"):
        return FailuresOutcome(prompts.NO_PAYMENTS_TOOL)
    system = prompts.failures_system(deps.now, request.window_days)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await reader.loop(deps, "payments", messages, FAILURES_LOOP_CALLS)
    status = reader.status("payments")
    if not status.ok:
        return FailuresOutcome(f"{prompts.NO_PAYMENTS_TOOL}\n\n{coverage.line(status)}")
    if result.status in _PAUSED:
        return FailuresOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    notes = (prompts.STEP_LIMIT_NOTE,) if hit_limit else ()
    parsed = None if hit_limit else fl.parse_failures(result.text)
    if parsed is None and (reader.fenced.seen_text or not hit_limit):
        parsed = await _repair(deps, system, query, result, reader.fenced.seen_text)
    return _checked(deps, parsed, request, reader.fenced.seen_text, notes, hit_limit)


def _checked(deps: TurnDeps, parsed: fl.FailuresAnswer | None, request: fl.Request, seen_text: str,
             notes: tuple[str, ...], hit_limit: bool) -> FailuresOutcome:
    if parsed is None:
        return FailuresOutcome(prompts.STEP_LIMIT_NOTHING if hit_limit else prompts.UNREADABLE_ANSWER)
    if parsed.status == "off_topic":
        return FailuresOutcome(smalltalk.OFF_TOPIC_REPLY)
    if parsed.status == "no_read_tool":
        return FailuresOutcome(prompts.NO_PAYMENTS_TOOL)
    checked = checks.post_check(parsed.failures, now=deps.now, request=request, seen_text=seen_text)
    if checked.dropped:
        log.info("post-check dropped %d failure(s) (unbacked, malformed or outside the window)", checked.dropped)
    if not checked.failures:
        nothing = prompts.STEP_LIMIT_NOTHING if hit_limit else prompts.nothing_failed(request.window_days)
        filtered = f"{checked.filtered} left out by your filters." if checked.filtered else ""
        return FailuresOutcome(" ".join(p for p in (nothing, filtered) if p))
    return FailuresOutcome(None, checked, checks.livemode(seen_text), notes, salvaged=hit_limit)


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult,
                  seen_text: str) -> fl.FailuresAnswer | None:
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("failures answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return fl.parse_failures(await ask_once(deps, messages))


# --- history --------------------------------------------------------------------------------------------

async def enrich(deps: TurnDeps, reader: Reader, items: Sequence[fl.Failure]) -> tuple[tuple[fl.Failure, ...], bool]:
    """(failures with history merged, whether the history step produced a readable answer)."""
    customers = list(dict.fromkeys(f.customer_id for f in items))[:checks.MAX_ENRICHED_CUSTOMERS]
    listing = json.dumps([{"customer_id": c, "email": next(f.customer_email for f in items if f.customer_id == c)}
                          for c in customers], ensure_ascii=False, indent=1)
    messages = ({"role": "system", "content": prompts.enrich_system(deps.now)},
                {"role": "user", "content": prompts.fence_candidates(listing)})
    result = await reader.loop(deps, "payments", messages, ENRICH_LOOP_CALLS)
    if result.status != "completed":
        log.info("history step ended %s; history not checked", result.status)
        return tuple(items), False
    histories = fl.parse_histories(result.text)
    seen_text = reader.fenced.seen_text
    monthly = {h.customer_id: scoring.monthly_minor(checks.verified_plan_amount(h, seen_text), h.interval,
                                                    h.interval_count) for h in histories}
    wanted = set(customers)
    merged = checks.apply_histories([f for f in items if f.customer_id in wanted], histories, seen_text, monthly)
    by_key = {f.key: f for f in merged}
    return tuple(by_key.get(f.key, f) for f in items), True


# --- prior contact ---------------------------------------------------------------------------------------

async def contact(deps: TurnDeps, reader: Reader, items: Sequence[fl.Failure]) -> tuple[fl.Failure, ...] | None:
    """Failures with prior contact applied, or None when the step could not run to the end."""
    listing = json.dumps([{"customer_email": f.customer_email, "invoice_id": f.invoice_id or f.id} for f in items],
                         ensure_ascii=False, indent=1)
    messages = ({"role": "system", "content": prompts.contact_system(deps.now)},
                {"role": "user", "content": prompts.fence_candidates(listing)})
    result = await reader.loop(deps, "email", messages, CONTACT_LOOP_CALLS)
    if result.status != "completed" or not reader.status("email").ok:
        log.info("contact step ended %s; prior contact not checked", result.status)
        return None
    return checks.apply_contacts(items, fl.parse_contacts(result.text), reader.fenced.seen_text, deps.now)


def unchecked_contact(items: Sequence[fl.Failure]) -> tuple[fl.Failure, ...]:
    return tuple(replace(f, contact_checked=False, contacted_at=None, contact_link=None) for f in items)


# --- channels ----------------------------------------------------------------------------------------------

async def list_channels(deps: TurnDeps, reader: Reader) -> tuple[str, ...]:
    """Channel names a chat tool returned this turn (verbatim), at most ten; () when none could be read."""
    if not reader.tools_for("chat"):
        return ()
    messages = ({"role": "system", "content": prompts.channels_system()},
                {"role": "user", "content": "List my chat channels."})
    result = await reader.loop(deps, "chat", messages, CHANNELS_LOOP_CALLS)
    if result.status != "completed":
        return ()
    seen = checks.normalise_text(reader.fenced.seen_text)
    names = tuple(n for n in fl.parse_channels(result.text) if checks.appears_in(n, seen))
    return names[:MAX_CHANNEL_OPTIONS]
