"""The read steps of a fresh case, each a bounded tool loop post-checked in code: payment (the charge, other
recent charges, or candidate customers), customer (plan, invoices, refunds, disputes), dispute (one by id, or
the open ones) and mail (the conversation with the customer). Also the shared read side (`Reader`) and the
tool-less single call (`ask_once`)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import case as cs
import checks
import coverage
import prompts
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("refund_dispute.steps")

PAYMENT_LOOP_CALLS = 5   # plus one JSON repair
CUSTOMER_LOOP_CALLS = 4
DISPUTE_LOOP_CALLS = 3
MAIL_LOOP_CALLS = 5
LLM_TIMEOUT_SECS = 45.0
MAIL_TIME_BUDGET_SECS = 210  # the mail step is skipped after this
_PAUSED = ("approval_pending", "authorization_required")


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping, traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1,
                                 timeout=LLM_TIMEOUT_SECS)
    return result.text.strip()


@dataclass(frozen=True)
class Reader:
    """One turn's read side: every read tool, the fence adapter that records tool output, and the failure
    tracker in front of it. Each step is offered only its own source's tools; none is a write tool."""

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

    def evidence(self, query: str) -> checks.Evidence:
        """This turn's tool output plus the user's own message: what every value must appear in."""
        return checks.Evidence(f"{self.fenced.seen_text}\n{query}")

    async def loop(self, deps: TurnDeps, source: str, messages: Sequence[Mapping], max_iterations: int) -> LoopResult:
        return await run_tool_loop(deps.llm, deps.model, messages, self.tools_for(source), self.health,
                                   deps.traceparent, max_iterations=max_iterations, is_offered=self.health.is_offered,
                                   timeout=LLM_TIMEOUT_SECS)


@dataclass(frozen=True)
class PaymentOutcome:
    """A fixed reply that ends the turn, or the checked charge (with recent charges / candidates)."""

    reply: str | None = None
    charge: cs.Charge | None = None
    recent: tuple[cs.Charge, ...] = ()
    candidates: tuple[cs.Candidate, ...] = ()
    salvaged: bool = False


def hints(request: cs.Request) -> str:
    parts = [f"email addresses {', '.join(request.emails)}" if request.emails else "",
             f"ids {', '.join(request.ids)}" if request.ids else ""]
    return "; ".join(p for p in parts if p) or "no email address or id"


async def payment(deps: TurnDeps, reader: Reader, query: str, request: cs.Request,
                  charge_hint: str | None = None) -> PaymentOutcome:
    if not reader.tools_for("payments"):
        return PaymentOutcome(prompts.NO_PAYMENTS_TOOL)
    found = hints(request) + (f"; the disputed charge is {charge_hint}" if charge_hint else "")
    system = prompts.payment_system(deps.now, found)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": prompts.fence_request(query)})
    result = await reader.loop(deps, "payments", messages, PAYMENT_LOOP_CALLS)
    status = reader.status("payments")
    if not status.ok:
        return PaymentOutcome(f"{prompts.NO_PAYMENTS_TOOL}\n\n{coverage.line(status)}")
    if result.status in _PAUSED:
        return PaymentOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    parsed = None if hit_limit else cs.parse_payment(result.text)
    if parsed is None and (reader.fenced.seen_text or not hit_limit):
        parsed = await _repair(deps, messages, result, reader.fenced.seen_text)
    if parsed is None:
        return PaymentOutcome(prompts.UNREADABLE_ANSWER)
    if parsed.status == "off_topic":
        return PaymentOutcome("off_topic")
    if parsed.status == "no_read_tool":
        return PaymentOutcome(prompts.NO_PAYMENTS_TOOL)
    ev = reader.evidence(query)
    charge = checks.charge(parsed.charge, ev)
    if charge is None and parsed.charge is not None:
        log.info("post-check dropped the charge (unbacked id, amount or date)")
    return PaymentOutcome(None, charge, checks.recent_charges(parsed.recent_charges, ev, charge.id if charge else None),
                          checks.candidates(parsed.candidates, ev), salvaged=hit_limit)


async def _repair(deps: TurnDeps, messages: Sequence[Mapping], result: LoopResult,
                  seen_text: str) -> cs.PaymentAnswer | None:
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("payment answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    repair = ({"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                          f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},)
    return cs.parse_payment(await ask_once(deps, (*messages, *repair)))


async def customer(deps: TurnDeps, reader: Reader, query: str, customer_id: str | None,
                   email: str | None) -> cs.CustomerFile | None:
    """The checked customer file, or None when the step could not finish."""
    listing = json.dumps({"customer_id": customer_id, "email": email}, ensure_ascii=False)
    messages = ({"role": "system", "content": prompts.customer_system(deps.now)},
                {"role": "user", "content": prompts.fence_case(listing)})
    result = await reader.loop(deps, "payments", messages, CUSTOMER_LOOP_CALLS)
    parsed = cs.parse_customer(result.text) if result.status == "completed" else None
    if parsed is None:
        log.info("customer step ended %s; history not checked", result.status)
        return None
    return checks.customer_file(parsed, reader.evidence(query))


async def disputes(deps: TurnDeps, reader: Reader, query: str, ids: Sequence[str],
                   list_mode: bool) -> tuple[cs.Dispute, ...] | None:
    messages = ({"role": "system", "content": prompts.dispute_system(deps.now, ", ".join(ids), list_mode)},
                {"role": "user", "content": prompts.fence_request(query)})
    result = await reader.loop(deps, "payments", messages, DISPUTE_LOOP_CALLS)
    parsed = cs.parse_disputes(result.text) if result.status == "completed" else None
    if parsed is None:
        log.info("dispute step ended %s", result.status)
        return None
    return checks.disputes(parsed, reader.evidence(query))


async def mail(deps: TurnDeps, reader: Reader, query: str, email: str) -> tuple[cs.Msg, ...] | None:
    """Verified messages with the customer, or None when the step could not finish."""
    messages = ({"role": "system", "content": prompts.mail_system(deps.now)},
                {"role": "user", "content": prompts.fence_case(json.dumps({"customer_email": email}))})
    result = await reader.loop(deps, "email", messages, MAIL_LOOP_CALLS)
    if result.status != "completed" or not reader.status("email").ok:
        log.info("mail step ended %s; history not checked", result.status)
        return None
    parsed = cs.parse_messages(result.text)
    if parsed is None:
        return None
    kept, dropped = checks.messages(parsed, reader.evidence(query))
    if dropped:
        log.info("post-check dropped %d message(s) without a verbatim link", dropped)
    return kept


def pasted_case(facts: Mapping[str, str], now: datetime) -> tuple[cs.Charge | None, cs.CustomerFile]:
    """The pasted-facts fallback: the same records, marked as pasted, nothing verified against a tool."""
    currency = (facts.get("currency") or "USD").strip().upper()[:3]
    amount = cs.to_minor(facts.get("amount", ""), currency)
    when = checks.parse_time(facts.get("date"))
    if amount is None or when is None or when > now:
        return None, cs.CustomerFile()
    email = (facts.get("customer_email") or "").strip().lower()
    charge = cs.Charge(id=(facts.get("charge_id") or "").strip() or "pasted charge", amount_minor=amount,
                       currency=currency, created_at=when.isoformat(), status="succeeded", source=cs.PASTED_SOURCE)
    plan = (facts.get("plan") or "").strip()
    yearly = plan.casefold() in ("annual", "yearly", "year", "annually") or "annual" in plan.casefold()
    subs = (cs.Subscription(id="pasted plan", plan_name=plan, interval="year" if yearly else "month",
                            amount_minor=amount),) if plan else ()
    customer = cs.Customer(id="pasted", email=email) if cs.EMAIL.fullmatch(email or "") else None
    count = cs.int_of(facts.get("prior_refunds"))
    return charge, cs.CustomerFile(customer=customer, subscriptions=subs,
                                   prior_refund_count=count if count is not None and count >= 0 else 0)
