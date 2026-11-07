"""Pure: the output envelope `{summary, items, confidence, needs_human}` plus this agent's `agenda`,
`stats`, `days` and `coverage`. `confidence` and `needs_human` are computed here from checks, never taken
from the model."""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import tzinfo

import render
from events import attendees_shown
from models import DayPlan, DayStats, Event, Gap, Plan


def confidence(plan: Plan) -> str:
    """low: only pasted data; medium: a context line dropped, a calendar tool failed, or the zone was
    asked; high otherwise."""
    if plan.pasted:
        return "low"
    if plan.context_dropped or plan.tools_failed or plan.coverage or plan.tz_asked:
        return "medium"
    return "high"


def needs_human(plan: Plan) -> bool:
    """A double booking, an unanswered invitation, or a timezone that had to be asked."""
    events = [e for d in plan.days for e in d.events]
    return plan.tz_asked or any(d.stats.double_bookings for d in plan.days) or any(
        e.status == "needs_response" for e in events)


def build(plan: Plan, tz: tzinfo) -> dict:
    numbers = {id(e): n for n, e in render.numbered(plan)}
    agenda = [entry for day in plan.days for entry in _agenda(day, tz, numbers)]
    return {
        "summary": " ".join(render.headline(d, tz) + "." for d in plan.days),
        "items": agenda,
        "confidence": confidence(plan),
        "needs_human": needs_human(plan),
        "agenda": agenda,
        "stats": asdict(_total(plan.days)),
        "days": [{"date": d.day.isoformat(), "stats": asdict(d.stats), "working_hours": list(d.hours),
                  "best_focus_block": _gap(d.best, d, tz) if d.best else None} for d in plan.days],
        "timezone": plan.zone.get("name") or ("as pasted" if plan.pasted else "UTC"),
        "coverage": list(plan.coverage),
    }


def _agenda(day: DayPlan, tz: tzinfo, numbers: dict) -> list[dict]:
    items = [_event(e, day, tz, numbers[id(e)]) for e in day.events] + [_gap(g, day, tz) for g in day.gaps]
    items.sort(key=lambda i: (i["start"], 0 if i["type"] == "event" else 1))
    return [*items, *({"type": "all_day", "date": day.day.isoformat(), "id": a.id, "title": a.title}
                      for a in day.all_day)]


def _event(e: Event, day: DayPlan, tz: tzinfo, n: int) -> dict:
    raw = e.raw
    return {"type": "event", "date": day.day.isoformat(), "n": n, "id": raw.id, "title": raw.title,
            "start": e.start.astimezone(tz).isoformat(), "end": e.end.astimezone(tz).isoformat(),
            "duration_min": e.minutes, "attendees": attendees_shown(raw), "organizer": raw.organizer or None,
            "location": raw.location or None, "join_link": raw.join_link or None, "status": e.status,
            "context": e.context, "evidence": e.evidence, "flags": list(e.flags), "notes": list(e.notes),
            "link": raw.link or None}


def _gap(g: Gap, day: DayPlan, tz: tzinfo) -> dict:
    return {"type": "gap", "date": day.day.isoformat(), "start": g.start.astimezone(tz).isoformat(),
            "end": g.end.astimezone(tz).isoformat(), "minutes": g.minutes,
            "after_event_id": g.after_event_id or None, "before_event_id": g.before_event_id or None}


def _total(days: tuple[DayPlan, ...]) -> DayStats:
    sums = {f.name: sum(getattr(d.stats, f.name) for d in days) for f in fields(DayStats)}
    sums["longest_gap_min"] = max((d.stats.longest_gap_min for d in days), default=0)
    return DayStats(**sums)
