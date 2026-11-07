"""One scan, end to end: pasted rows, or discover → subscriptions → changes → flag (code) → mail → desk
→ score (code). Returns the ranked rows and the list's metadata, or a fixed reply that ends the turn.

`confidence` and `needs_human` are computed here from what ran, never taken from the model."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

import pasted
import prompts
import scoring
import signals
import smalltalk
import steps
from deps import TurnDeps
from discovery import discover
from reader import HELPERS, Reader, ask_once
from render import ListMeta
from request import Request
from scoring import Ranked, Row
from signals import DESK, EMAIL, Account

log = logging.getLogger("churn.scan")

TIME_BUDGET_SECS = 270  # mail and desk are skipped once a turn has run this long (turn deadline 360 s)
_NO_SOURCE = (prompts.NO_PAYMENTS_TOOL, prompts.GATEWAY_UNREACHABLE)
_SCOPE_CHECK = (f"{prompts.SCOPE}\nClassify the user's message. Questions about customers, accounts, churn, "
                "retention, renewals, cancellations, subscriptions, revenue, payments or outreach to customers "
                f"are IN_SCOPE. Reply exactly IN_SCOPE or exactly {prompts.OFF_TOPIC}, nothing else.")
# Words that put a message plainly in scope: no model call is needed to keep it.
_IN_SCOPE = re.compile(r"\b(?:churn\w*|retention|renew\w*|cancel\w*|subscri\w*|mrr|arr|revenue|customers?|"
                       r"accounts?|downgrad\w*|outreach|past.?due|invoice\w*|payments?|at.?risk|trials?)\b",
                       re.IGNORECASE)


@dataclass(frozen=True)
class ScanOutcome:
    reply: str | None
    rows: tuple[Row, ...] = ()
    meta: ListMeta | None = None


async def run(deps: TurnDeps, query: str, req: Request) -> ScanOutcome:
    rows = pasted.find_rows(query)
    if rows is not None:
        return _from_pasted(deps, rows, req)
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return ScanOutcome(await _declined_or(deps, query, failure) if failure in _NO_SOURCE else failure)
    reader = Reader.over(deps, catalog)
    if not any(catalog.placed(t) == "payments" for t in reader.tools("payments") if t.name not in HELPERS):
        return ScanOutcome(await _declined_or(deps, query, prompts.NO_PAYMENTS_TOOL))
    subs = await steps.read_subs(deps, reader, query, req)
    if subs.reply is not None:
        return ScanOutcome(subs.reply)
    changes, failures, changes_note = await steps.read_changes(deps, reader, subs.subs, req)
    accounts = signals.billing_accounts(subs.subs, changes, failures, deps.today, req.lookahead_days,
                                        req.lookback_days)
    flagged = tuple(a for a in accounts if a.signals)
    mail = await _side(deps, steps.read_mail, reader, flagged)
    desk = await _side(deps, steps.read_desk, reader, flagged)
    checked = tuple(_cross_checked(a, mail, desk, deps) for a in flagged)
    unchecked = tuple(f"{label} ({side.note})" for label, side in ((EMAIL, mail), (DESK, desk)) if side.data is None)
    notes = (*subs.notes, *(n for n in (changes_note,) if n),
             *((prompts.EVENTS_RETENTION_NOTE,) if req.lookback_days > 30 else ()))
    ranked = scoring.rank(checked, deps.today, req.min_mrr, req.focus)
    degraded = (mail.data is None or (desk.data is None and desk.note != "not connected") or bool(changes_note)
                or bool(subs.dropped))
    confidence = "low" if subs.salvaged else "medium" if degraded else "high"
    meta = _meta(req, ranked, notes, unchecked, confidence, _needs_human(ranked, checked, mail.data is None),
                 dropped=subs.dropped)
    return ScanOutcome(None, ranked.rows, meta)


async def _side(deps: TurnDeps, step, reader: Reader, flagged: Sequence[Account]) -> steps.SideRead:
    if deps.elapsed() > TIME_BUDGET_SECS:
        log.info("scan at %.0fs; skipping %s", deps.elapsed(), step.__name__)
        return steps.SideRead(None, prompts.TIME_SKIPPED)
    return await step(deps, reader, flagged)


def _cross_checked(account: Account, mail: steps.SideRead, desk: steps.SideRead, deps: TurnDeps) -> Account:
    with_mail = signals.with_mail(account, steps.mail_for(mail.data, account.id), deps.today)
    return signals.with_desk(with_mail, steps.tickets_for(desk.data, account.id), deps.today)


def _needs_human(ranked: Ranked, accounts: Sequence[Account], mail_unchecked: bool) -> bool:
    if mail_unchecked or any("complaint" in r.account.kinds for r in ranked.rows):
        return True
    floor = scoring.top_quartile_floor(accounts)
    return floor is not None and any(r.band == "High" and scoring.mrr.to_major(r.account.mrr_minor,
                                                                                r.account.currency) >= floor
                                     for r in ranked.at_risk)


def _meta(req: Request, ranked: Ranked, notes: Sequence[str], unchecked: Sequence[str], confidence: str,
          needs_human: bool, mode: str = "live", dropped: int = 0, skipped: int = 0) -> ListMeta:
    return ListMeta(lookahead=req.lookahead_days, lookback=req.lookback_days, mode=mode,
                    healthy=ranked.healthy_renewals, below_min=ranked.below_minimum, left_out=ranked.left_out,
                    dropped=dropped, skipped_rows=skipped, min_mrr=f"{req.min_mrr:,}" if req.min_mrr is not None else "",
                    notes=tuple(notes), unchecked=tuple(unchecked), confidence=confidence, needs_human=needs_human)


def _from_pasted(deps: TurnDeps, rows: Sequence[dict], req: Request) -> ScanOutcome:
    """Pasted billing rows: ranked in code, no gateway and no model call."""
    read = pasted.accounts(rows, deps.today, req.lookahead_days, req.lookback_days)
    if not read.accounts:
        return ScanOutcome("I couldn't read any row with an account name and an MRR.\n\n" + prompts.PASTE_HINT)
    ranked = scoring.rank(read.accounts, deps.today, req.min_mrr, req.focus)
    unchecked = (f"{EMAIL} (pasted rows)", f"{DESK} (pasted rows)")
    meta = _meta(req, ranked, (), unchecked, "low", True, mode="pasted", skipped=read.skipped)
    return ScanOutcome(None, ranked.rows, meta)


async def _declined_or(deps: TurnDeps, query: str, refusal: str) -> str:
    """No source to read: an off-topic message is still declined (one tool-less call), else the refusal."""
    if _IN_SCOPE.search(query or ""):
        return refusal
    if not deps.traceparent:
        return prompts.NO_TRACE_CONTEXT
    verdict = await ask_once(deps, ({"role": "system", "content": _SCOPE_CHECK}, {"role": "user", "content": query}))
    log.info("scope check without a payments source: %s", verdict[:20])
    off = verdict.startswith(prompts.OFF_TOPIC) and "IN_SCOPE" not in verdict
    return smalltalk.OFF_TOPIC_REPLY if off else refusal
