"""One pull: tickets (desk or pasted) → previous-period count → SLA targets → themes → digest. Every number
on the way is computed in code; the model only reads the desk and labels subjects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

import digest
import prompts
import replies
import smalltalk
import stats
import themes
from budget import TurnLimitReached
from cluster import cluster, distinct
from deps import TurnDeps
from parse_paste import looks_pasted, parse_pasted
from period import Period, previous
from reader import TurnReader, ask_once
from records import Ticket
from request import Request
from sla import targets
from steps_sla import sla_step
from steps_tickets import current_step, previous_step, select

_MAX_SCOPE_CHARS = 4000


@dataclass(frozen=True)
class Pull:
    reply: str
    envelope: dict | None = None
    off_topic: bool = False


async def in_scope(deps: TurnDeps, query: str) -> bool:
    messages = ({"role": "system", "content": prompts.SCOPE_SYSTEM},
                {"role": "user", "content": query[:_MAX_SCOPE_CHARS]})
    return not (await ask_once(deps, messages)).upper().startswith(prompts.OFF_TOPIC)


def pasted_period(tickets: tuple[Ticket, ...], request: Request, days: int) -> Period:
    """A named period as asked; otherwise the default length ending on the newest pasted ticket's day."""
    created = [d for d in (stats.created_day(t) for t in tickets) if d is not None]
    if request.explicit or not created:
        return request.period
    end = max(created)
    return Period(end - timedelta(days=days - 1), end)


def _pasted(deps: TurnDeps, query: str, request: Request) -> digest.Inputs:
    every = parse_pasted(query)
    p = pasted_period(every, request, deps.config.period_days)
    kept, capped = select(every, p, request.filters, deps.config.max_tickets)
    prior = previous(p)
    has_prior = any((d := stats.created_day(t)) is not None and d < p.start for t in every)
    prev = len([t for t in stats.in_period(every, prior) if request.filters.keeps(t)]) if has_prior else None
    notes = () if has_prior else (replies.PASTED_NO_PREVIOUS,)
    return digest.Inputs(tickets=kept, period=p, previous_total=prev, reported_total=None, targets=targets(()),
                         themes=(), capped=capped, extracted=len(every), notes=notes)


async def _from_desk(deps: TurnDeps, query: str, request: Request) -> digest.Inputs | Pull:
    try:
        if not await in_scope(deps, query):
            return Pull(smalltalk.OFF_TOPIC_REPLY, off_topic=True)
    except TurnLimitReached:
        return Pull(replies.STEP_LIMIT_NOTE)
    turn = TurnReader(deps)
    p = request.period
    found = await current_step(deps, turn, p, request.filters)
    if found.reply is not None:
        return Pull(found.reply)
    if not found.tickets:
        return Pull("\n\n".join([replies.NO_TICKETS.format(start=p.start, end=p.end), *found.notes]))
    prev = await previous_step(deps, turn, found.tools, previous(p), request.filters)
    tg = await sla_step(deps, turn)
    notes = found.notes + (() if prev.count is not None else (replies.PREVIOUS_UNREAD,))
    return digest.Inputs(tickets=found.tickets, period=p, previous_total=prev.count,
                         reported_total=found.reported_total, targets=tg, themes=(), capped=found.capped,
                         previous_sampled=prev.sampled, extracted=found.extracted, dropped=found.dropped,
                         salvaged=found.salvaged, notes=notes)


async def _with_themes(deps: TurnDeps, inputs: digest.Inputs) -> digest.Inputs:
    cfg = deps.config
    labelled = await themes.label(deps, inputs.tickets)
    clustering = await cluster(deps, distinct(labelled.labels.values(), cfg.max_labels))
    found = themes.count(inputs.tickets, labelled.labels, clustering.theme_of, len(inputs.tickets),
                         cfg.theme_min_count, cfg.top_n)
    with_subject = sum(bool(t.subject) for t in inputs.tickets)
    partial = len(labelled.labels) < with_subject and (labelled.failed_chunks > 0 or labelled.skipped > 0)
    notes = inputs.notes
    if partial:
        notes += (replies.THEMES_PARTIAL.format(covered=len(labelled.labels), total=len(inputs.tickets)),)
    if clustering.fallback:
        notes += (replies.CLUSTER_FALLBACK,)
    return replace(inputs, themes=found, themes_partial=partial or clustering.fallback, notes=notes)


async def pull(deps: TurnDeps, query: str, request: Request) -> Pull:
    if looks_pasted(query):
        gathered: digest.Inputs | Pull = _pasted(deps, query, request)
    else:
        gathered = await _from_desk(deps, query, request)
    if isinstance(gathered, Pull):
        return gathered
    if not gathered.tickets:
        p = gathered.period
        return Pull(replies.NO_TICKETS.format(start=p.start, end=p.end))
    full = await _with_themes(deps, gathered)
    text, env = digest.build(full, request, deps.now, deps.config)
    return Pull(text, env)

