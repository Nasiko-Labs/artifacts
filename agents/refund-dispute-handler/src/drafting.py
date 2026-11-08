"""The reply step: one tool-less LLM call that drafts the reply to the customer from the checked case and a
decision (the recommendation, or the user's different decision on the same facts). The recipient comes from
the checked customer; amounts and dates come from code; before the user confirms a refund in chat, an approve
or partial reply carries the placeholder instead of a confirmation. The send tool is chosen in code from the
email write tools the gateway grants; without one the draft is offered to copy."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass

import prompts
import render
from case import money
from deps import TurnDeps
from discovery import Catalog, discover
from drafts import (
    Draft,
    body_problem,
    parse_draft,
    reply_draft,
    strip_links,
    with_placeholder,
)
from nasiko_mcp import McpTool
from send_guard import name_tokens
from steps import ask_once

log = logging.getLogger("refund_dispute.drafting")

FINAL_OUTCOMES = frozenset({"sent", "denied"})  # never drafted again in this conversation


@dataclass(frozen=True)
class Written:
    subject: str = ""
    body: str = ""
    decision: str = ""
    confirmed: bool = False
    problem: str | None = None

    def to_dict(self) -> dict:
        return {"subject": self.subject, "body": self.body, "decision": self.decision, "confirmed": self.confirmed,
                "status": "ready" if self.confirmed else "pending_confirmation"}


@dataclass(frozen=True)
class ReplyTurn:
    reply: str
    drafts: tuple[Draft, ...] = ()


def reply_facts(env: Mapping, decision: str, amount_minor: int | None, confirmed: bool) -> dict:
    """What the draft model may use, all computed in code. The decision amount only after confirmation."""
    facts = env.get("facts") or {}
    currency = facts.get("currency") or ""
    policy = env.get("policy") or {}
    data = {"customer_name": env.get("customer_name"), "decision": decision,
            "charge": f"{money(facts.get('amount_minor'), currency)} on {facts.get('charge_date')}",
            "refund_window_days": policy.get("window_days"), "refund_window_ended": facts.get("window_end"),
            "days_since_charge": facts.get("days_since_charge"), "plan": facts.get("plan_name")}
    if confirmed and decision in ("approve", "partial"):
        data["refund_amount"] = money(amount_minor, currency)
    return data


def allowed_amounts(env: Mapping, amount_minor: int | None, confirmed: bool) -> tuple[str, ...]:
    facts = env.get("facts") or {}
    currency = facts.get("currency") or ""
    amounts = [money(facts.get("amount_minor"), currency)]
    if confirmed and amount_minor is not None:
        amounts.append(money(amount_minor, currency))
    return tuple(a.split(" ")[0] for a in amounts if a != "unknown")


async def write(deps: TurnDeps, env: Mapping, decision: str, amount_minor: int | None, instruction: str,
                confirmed: bool) -> Written:
    """One LLM call, then the code checks; `problem` says why nothing usable came back."""
    recipient = env.get("customer_email") or ""
    data = reply_facts(env, decision, amount_minor, confirmed)
    system = f"{prompts.reply_system(decision, confirmed)}\n\n{prompts.fence_case(json.dumps(data, ensure_ascii=False))}"
    ask = instruction or "Draft the reply to the customer."
    text = parse_draft(await ask_once(deps, ({"role": "system", "content": system},
                                             {"role": "user", "content": prompts.fence_request(ask)})))
    if text is None:
        return Written(decision=decision, problem="the draft could not be read")
    body = with_placeholder(strip_links(text.body).strip(), decision, confirmed)
    problem = body_problem(body, recipient, allowed_amounts(env, amount_minor, confirmed), confirmed)
    if problem is not None:
        log.info("reply draft refused: %s", problem)
        return Written(decision=decision, problem=problem)
    return Written(text.subject or "Your refund request", body, decision, confirmed)


async def to_walk(deps: TurnDeps, env: Mapping, written: Written, key: str) -> ReplyTurn:
    """The approval card for a written reply, or the copyable text when no send tool is granted."""
    if written.problem is not None:
        return ReplyTurn(f"I couldn't draft the reply safely ({written.problem}); ask me again.")
    recipient = env.get("customer_email") or ""
    name = env.get("customer_name") or ""
    gist = env.get("summary") or "the refund request"
    catalog, failure = await discover(deps, "send")
    tool = send_tool(catalog) if failure is None else None
    if tool is None:
        reason = failure if failure and failure not in (prompts.NO_PAYMENTS_TOOL,) else prompts.NO_SEND_TOOL
        return ReplyTurn(render.render_copyable(f"Reply to {name or recipient}", recipient, written.subject,
                                                written.body, reason))
    draft = reply_draft(key, recipient, name, written.subject, written.body, tool.name,
                        env.get("thread_ref") or "", written.decision, gist)
    return ReplyTurn("", (draft,))


def send_tool(catalog: Catalog) -> McpTool | None:
    """The email write tool, chosen in code: one whose own words place it in email, preferring send/reply."""
    tools = catalog.send_tools("email")
    sending = [t for t in tools if {"send", "reply"} & set(name_tokens(t.name)) and "draft" not in name_tokens(t.name)]
    return next(iter(sending or tools), None)
