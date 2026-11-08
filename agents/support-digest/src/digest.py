"""Pure assembly of the digest from what the steps read: volume and trend, themes, SLA, customers, backlog,
notes and the envelope. Every number here comes from stats.py and sla.py."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

import envelope
import render
import replies
import sla
import stats
from config import Config
from period import Period
from records import Breach, Theme, Ticket
from request import Request


@dataclass(frozen=True)
class Inputs:
    """What one pull gathered, ready to compute on."""

    tickets: tuple[Ticket, ...]
    period: Period
    previous_total: int | None
    reported_total: int | None
    targets: sla.Targets
    themes: tuple[Theme, ...]
    source_read: bool = True
    capped: bool = False
    previous_sampled: bool = False
    extracted: int = 0
    dropped: int = 0
    salvaged: bool = False
    themes_partial: bool = False
    notes: tuple[str, ...] = ()


def breach_dict(b: Breach) -> dict:
    return {"ticket_id": b.ticket_id, "kind": b.kind, "open": b.open, "overrun_hours": sla.overrun_hours(b),
            "priority": b.priority, "customer": b.customer, "url": b.url}


def _sla_source_line(t: sla.Targets) -> str:
    if t.source == "defaults":
        return replies.SLA_DEFAULTS_NOTE + "."
    extra = f" ({replies.SLA_PARTLY_NOTE.format(names=', '.join(t.defaulted))})" if t.defaulted else ""
    return f"{replies.SLA_DESK_NOTE}{extra}."


def _notes(i: Inputs, request: Request, s: sla.SlaResult, vol: stats.Volume, config: Config) -> tuple[str, ...]:
    notes = list(i.notes)
    if request.filters.active:
        notes.append(replies.FILTER_NOTE.format(what=request.filters.describe()))
    if request.ambiguous:
        notes.append(replies.AMBIGUOUS_NOTE)
    if vol.total > len(i.tickets):
        notes.append(replies.SAMPLE_NOTE.format(total=vol.total, n=len(i.tickets)))
    elif i.capped:
        notes.append(replies.CAP_NOTE.format(n=config.max_tickets))
    if i.previous_sampled:
        notes.append(replies.PREVIOUS_SAMPLED)
    notes.append(_sla_source_line(i.targets) + " " + replies.CALENDAR_NOTE)
    if s.unknown_times:
        notes.append(replies.UNKNOWN_NOTE.format(n=s.unknown_times))
    if i.dropped:
        notes.append(replies.DROPPED_NOTE.format(n=i.dropped))
    return tuple(dict.fromkeys(notes))


def build(i: Inputs, request: Request, now: datetime, config: Config) -> tuple[str, dict]:
    vol = stats.volume(i.tickets, i.period, i.previous_total, i.reported_total)
    s = sla.assess(i.tickets, i.targets, now)
    sampled = vol.total > len(i.tickets) or i.capped
    digest = {
        "period": i.period.to_dict(), "volume": vol.to_dict(),
        "busiest": {"date": vol.busiest[0], "count": vol.busiest[1]} if vol.busiest else None,
        "themes": [asdict(t) for t in i.themes],
        "sla": {"source": i.targets.source, "first_reply_breaches": s.first_reply_breaches,
                "first_reply_rate_pct": s.first_reply_rate, "resolution_breaches": s.resolution_breaches,
                "open_breaches": s.open_breaches, "at_risk_soon": s.at_risk_soon, "unknown_times": s.unknown_times,
                "worst": [breach_dict(b) for b in sla.worst(s)]},
        "top_customers": list(stats.top_customers(i.tickets, config.top_n)),
        "backlog": stats.backlog(i.tickets), "sampled": sampled, "filters": request.filters.describe(),
    }
    notes = _notes(i, request, s, vol, config)
    text = render.report(digest, notes, config.theme_min_count)
    checks = envelope.Checks(source_read=i.source_read, previous_read=i.previous_total is not None,
                             sla_from_desk=i.targets.source == "desk", capped=sampled or i.previous_sampled,
                             extracted=i.extracted, dropped=i.dropped, salvaged=i.salvaged,
                             themes_partial=i.themes_partial, ambiguous=request.ambiguous,
                             open_breaches=s.open_breaches)
    breaches: Sequence[dict] = [breach_dict(b) for b in s.breaches[:config.max_breach_lines]]
    env = envelope.build(render.headline(digest), digest, text, breaches, checks, notes)
    return text, env
