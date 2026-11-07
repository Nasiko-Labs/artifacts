"""The read steps of a scan, each a bounded tool loop with its own JSON contract, checked in code:

    subscriptions (8 calls + 1 repair) → changes (4) → mail for flagged accounts (6) → support desk (4)

Only the subscriptions step can end the turn (no payments data means nothing to rank). The others
degrade: their signals become "not checked", with one coverage line."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import checks
import coverage
import prompts
import records
import smalltalk
from deps import TurnDeps
from reader import Reader, ask_once
from records import Change, Failure, MailRecord, Sub, Ticket
from request import Request
from signals import Account

log = logging.getLogger("churn.steps")

SUBS_CALLS = 8
CHANGES_CALLS = 4
MAIL_CALLS = 6
DESK_CALLS = 4
MAX_MAIL_ACCOUNTS = 15
_PAUSED = ("approval_pending", "authorization_required")


@dataclass(frozen=True)
class SubsOutcome:
    """Either a fixed reply that ends the turn, or the checked subscriptions."""

    reply: str | None
    subs: tuple[Sub, ...] = ()
    dropped: int = 0
    salvaged: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SideRead:
    """An optional step's result: `None` data means not checked, with the short reason in `note`."""

    data: dict | None
    note: str = ""


async def read_subs(deps: TurnDeps, reader: Reader, query: str, req: Request) -> SubsOutcome:
    system = prompts.subs_system(deps.now, req.lookahead_days, req.lookback_days)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    mark = reader.fenced.mark()
    result = await reader.loop(deps, messages, reader.tools("payments"), SUBS_CALLS)
    status = reader.status("payments")
    if status.state == "unavailable":
        return SubsOutcome(coverage.payments_refused(status))
    if result.status in _PAUSED:
        return SubsOutcome(prompts.READ_NEEDS_APPROVAL)
    if records.is_off_topic(result.text):
        return SubsOutcome(smalltalk.OFF_TOPIC_REPLY)
    hit_limit = result.status == "max_iterations"
    seen = reader.fenced.seen_since(mark)
    parsed = None if hit_limit else records.parse_subscriptions(result.text)
    repaired = parsed is None and bool(seen)
    if repaired:
        parsed = await _repair(deps, system, query, result.text, seen)
    if parsed is None:
        return SubsOutcome(prompts.STEP_LIMIT_NOTHING if hit_limit else prompts.UNREADABLE_ANSWER)
    checked = checks.check_subs(parsed, seen)
    if checked.dropped:
        log.info("post-check dropped %d subscription(s) not backed by tool output", checked.dropped)
    if parsed and not checked.subs:
        return SubsOutcome(prompts.UNREADABLE_ANSWER)
    notes = (prompts.STEP_LIMIT_NOTE,) if hit_limit else ()
    return SubsOutcome(None, checked.subs, checked.dropped, hit_limit or repaired, notes)


async def _repair(deps: TurnDeps, system: str, query: str, answer: str, seen: str) -> tuple[Sub, ...] | None:
    """One tool-less call that restates the answer as JSON from this step's (fenced) tool output."""
    log.info("subscriptions answer was not the agreed JSON; asking once for a rewrite")
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this turn:\n{prompts.fence(seen)}\n\nYour previous answer:\n"
                                    f"{prompts.fence(answer)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return records.parse_subscriptions(await ask_once(deps, messages))


async def read_changes(deps: TurnDeps, reader: Reader, subs: Sequence[Sub],
                       req: Request) -> tuple[tuple[Change, ...], tuple[Failure, ...], str]:
    """Change events and failed payments, or ((), (), note) when they could not be read."""
    ids = json.dumps([{"id": s.id, "customer_id": s.customer_id} for s in subs], ensure_ascii=False)
    messages = ({"role": "system", "content": prompts.changes_system(deps.now, req.lookback_days, ids)},
                {"role": "user", "content": prompts.fence_candidates(ids)})
    mark = reader.fenced.mark()
    result = await reader.loop(deps, messages, reader.tools("payments"), CHANGES_CALLS)
    parsed = records.parse_changes(result.text) if result.status == "completed" else None
    if parsed is None:
        log.info("change step ended %s without a readable answer", result.status)
        return (), (), prompts.CHANGES_NOT_READ
    changes, failures, dropped = checks.check_changes(*parsed, subs, reader.fenced.seen_since(mark))
    if dropped:
        log.info("post-check dropped %d change(s)/failure(s)", dropped)
    return changes, failures, ""


def mail_candidates(accounts: Sequence[Account]) -> tuple[Account, ...]:
    """The accounts the mail step asks about: flagged, with an address, top 15 by MRR."""
    with_email = [a for a in accounts if a.email]
    return tuple(sorted(with_email, key=lambda a: (-a.mrr_minor, a.name.casefold()))[:MAX_MAIL_ACCOUNTS])


async def read_mail(deps: TurnDeps, reader: Reader, accounts: Sequence[Account]) -> SideRead:
    """Mail records by account id for the candidates the step answered for."""
    before = reader.status("email")
    if before.state != "ok":
        return SideRead(None, coverage.reason(before))
    candidates = mail_candidates(accounts)
    if not candidates:
        return SideRead({})
    listing = json.dumps([_mail_view(a) for a in candidates], ensure_ascii=False)
    messages = ({"role": "system", "content": prompts.mail_system(deps.now)},
                {"role": "user", "content": prompts.fence_candidates(listing)})
    mark = reader.fenced.mark()
    result = await reader.loop(deps, messages, reader.tools("email"), MAIL_CALLS)
    after = reader.status("email")
    if after.state != "ok":
        return SideRead(None, coverage.reason(after))
    parsed = records.parse_mail(result.text) if result.status == "completed" else None
    if parsed is None:
        return SideRead(None, "could not be read this time")
    emails = {a.id: a.email for a in candidates}
    return SideRead(checks.check_mail(parsed, emails, reader.fenced.seen_since(mark)))


def _mail_view(a: Account) -> dict:
    domain = checks.domain_of(a.email)
    shared = domain in checks.FREE_MAIL_DOMAINS
    return {"account_id": a.id, "name": a.name, "email": a.email,
            "match": f"address {a.email}" if shared or not domain else f"domain {domain}"}


async def read_desk(deps: TurnDeps, reader: Reader, accounts: Sequence[Account]) -> SideRead:
    """Tickets by account id for the accounts asked about (an account with none checked as none)."""
    before = reader.status("support")
    if before.state != "ok":
        return SideRead(None, coverage.reason(before))
    candidates = mail_candidates(accounts)
    if not candidates:
        return SideRead({})
    listing = json.dumps([{"account_id": a.id, "name": a.name, "email": a.email} for a in candidates],
                         ensure_ascii=False)
    messages = ({"role": "system", "content": prompts.desk_system(deps.now)},
                {"role": "user", "content": prompts.fence_candidates(listing)})
    mark = reader.fenced.mark()
    result = await reader.loop(deps, messages, reader.tools("support"), DESK_CALLS)
    after = reader.status("support")
    if after.state != "ok":
        return SideRead(None, coverage.reason(after))
    parsed = records.parse_tickets(result.text) if result.status == "completed" else None
    if parsed is None:
        return SideRead(None, "could not be read this time")
    return SideRead(checks.check_tickets(parsed, [a.id for a in candidates], reader.fenced.seen_since(mark)))


def tickets_for(desk: dict | None, account_id: str) -> tuple[Ticket, ...] | None:
    return None if desk is None or account_id not in desk else desk[account_id]


def mail_for(mail: dict | None, account_id: str) -> MailRecord | None:
    return None if mail is None else mail.get(account_id)
