"""One pull: tickets (desk or pasted) → facts → sentiment → score → filter and rank → bugs → match, then
the rendered list and the envelope. Every number on the way is computed in code."""

from __future__ import annotations

from dataclasses import dataclass

import envelope
import facts
import matching
import prompts
import render
import replies
import risk
import sentiment
import smalltalk
from budget import TurnLimitReached
from deps import TurnDeps
from parse_paste import looks_pasted, parse_pasted
from reader import TurnReader, ask_once
from records import BugMatch, Ticket
from request import Request
from steps_bugs import BugsOutcome, bugs_step
from steps_tickets import TicketsOutcome, tickets_step

_MAX_SCOPE_CHARS = 4000


@dataclass(frozen=True)
class Pull:
    reply: str
    envelope: dict | None = None
    targets: tuple[dict, ...] = ()   # what a later status-reply draft needs, per scored ticket
    off_topic: bool = False


async def in_scope(deps: TurnDeps, query: str) -> bool:
    messages = ({"role": "system", "content": prompts.SCOPE_SYSTEM},
                {"role": "user", "content": query[:_MAX_SCOPE_CHARS]})
    return not (await ask_once(deps, messages)).upper().startswith(prompts.OFF_TOPIC)


async def pull(deps: TurnDeps, query: str, request: Request) -> Pull:
    turn = TurnReader(deps)
    pasted = looks_pasted(query)
    if pasted:
        found = TicketsOutcome(tickets=parse_pasted(query)[:deps.config.max_tickets])
    else:
        try:
            if not await in_scope(deps, query):
                return Pull(smalltalk.OFF_TOPIC_REPLY, off_topic=True)
        except TurnLimitReached:
            return Pull(replies.STEP_LIMIT_NOTE)
        found = await tickets_step(deps, turn, request.window_days)
        if found.reply is not None:
            return Pull(found.reply)
    if not found.tickets:
        return Pull("\n\n".join([replies.NO_TICKETS.format(days=request.window_days), *found.notes]))
    labels = await sentiment.label(deps, found.tickets)
    scored = [risk.assess(t, facts.facts_for(t, deps.now), labels.by_ticket.get(t.id), deps.config.escalation_phrases)
              for t in found.tickets]
    ranked = risk.rank([r for r in scored if _wanted(r, request)])
    shown = ranked[:request.top_n]
    by_id = {t.id: t for t in found.tickets}
    bugs = await bugs_step(deps, turn, [by_id[r.ticket_id] for r in shown[:deps.config.bug_tickets]])
    matches = tuple(m for r in shown[:deps.config.bug_tickets]
                    for m in matching.match(by_id[r.ticket_id], bugs.issues, deps.config.max_bugs_per_ticket))
    checks = _checks(found, bugs, labels.failed_batches, request)
    notes = _notes(found, bugs, deps)
    view = render.ListView(total=len(found.tickets), window_days=request.window_days, pasted=pasted,
                           high=sum(r.risk_level == "high" for r in ranked),
                           medium=sum(r.risk_level == "medium" for r in ranked), notes=notes)
    text = render.report(shown, matches, view)
    env = envelope.build(render.headline(shown, view), shown, matches, checks, notes)
    return Pull(text, env, _targets(found.tickets, scored, matches))


def _wanted(row: risk.Assessed, request: Request) -> bool:
    if request.focus_ids and row.ticket_id not in request.focus_ids:
        return False
    if request.tier and row.tier != request.tier:
        return False
    return not request.customer or request.customer.casefold() in row.customer.casefold()


def _checks(found: TicketsOutcome, bugs: BugsOutcome, failed: int, request: Request) -> envelope.Checks:
    missing = sum(not t.comments_known for t in found.tickets)
    return envelope.Checks(desk_read=True, tracker_read=bugs.checked, capped=found.capped or bugs.capped,
                           extracted=found.extracted + bugs.extracted, dropped=found.dropped + bugs.dropped,
                           salvaged=found.salvaged or bugs.salvaged, comments_missing=missing + failed,
                           ambiguous=request.ambiguous)


def _notes(found: TicketsOutcome, bugs: BugsOutcome, deps: TurnDeps) -> tuple[str, ...]:
    notes = list(found.notes)
    if found.capped:
        notes.append(replies.CAP_NOTE.format(n=deps.config.max_tickets))
    missing = sum(not t.comments_known for t in found.tickets)
    if missing:
        notes.append(replies.COMMENTS_NOTE.format(n=missing))
    if found.dropped + bugs.dropped:
        notes.append(replies.DROPPED_NOTE.format(n=found.dropped + bugs.dropped))
    notes.extend(n for n in bugs.notes if n not in notes)
    return tuple(notes)


def _targets(tickets: tuple[Ticket, ...], scored: list[risk.Assessed], matches: tuple[BugMatch, ...]) -> tuple:
    rows = {r.ticket_id: r for r in scored}
    return tuple({"ticket_id": t.id, "customer": rows[t.id].customer, "subject": t.subject,
                  "bugs": [{"title": m.title, "state": m.state, "closed": m.closed}
                           for m in matches if m.ticket_id == t.id]} for t in tickets)
