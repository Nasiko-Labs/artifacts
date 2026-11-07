"""Pure: the disagreement rules D1 to D11 over one evidence unit (a ticket, or an epic and its children).

Every date comparison uses the as-of date the caller passes; nothing here reads a clock. A ticket whose
`updated` cell could not be read is left out of every staleness rule (D5, D7, D10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import statuses as st
from dates import from_iso

HIGH, MEDIUM, LOW = "high", "medium", "low"
RISK_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}
RECENT_DAYS = 3
OLD_TICKET_DAYS = 14
EPIC_DONE_PCT = 0.9
EPIC_BEHIND_PCT = 0.5

RULES: dict[str, tuple[str, str]] = {
    "D1": ("reported_done_not_done", HIGH), "D2": ("on_track_but_blocked", HIGH),
    "D3": ("on_track_not_started", HIGH), "D4": ("on_track_overdue", HIGH), "D5": ("on_track_stale", MEDIUM),
    "D6": ("epic_behind", HIGH), "D7": ("blocked_but_moving", LOW), "D8": ("blocked_but_done", MEDIUM),
    "D9": ("not_started_but_started", LOW), "D10": ("at_risk_not_visible", LOW),
    "D11": ("ticket_reassigned_or_unowned", MEDIUM),
}


@dataclass(frozen=True)
class Ctx:
    as_of: date
    stale_days: int
    soon_days: int
    status_map: dict


@dataclass(frozen=True)
class Facts:
    category: str
    is_epic: bool
    n_children: int
    n_done: int
    n_blocked: int
    n_in_progress: int
    due: date | None
    updated: date | None
    created: date | None
    blocked_label: bool
    assignee: str | None

    @property
    def pct_done(self) -> float:
        return self.n_done / self.n_children if self.n_children else 0.0

    @property
    def started(self) -> bool:
        return self.n_done + self.n_in_progress > 0 if self.is_epic else self.category in (st.IN_PROGRESS, st.DONE)


def category(ticket: dict, ctx: Ctx) -> str:
    return st.ticket_category(ticket["status"], ticket.get("labels") or (), ctx.status_map)


def facts(ticket: dict, children: list[dict], ctx: Ctx) -> Facts:
    cats = [category(c, ctx) for c in children]
    stamps = [d for d in (from_iso(t.get("updated")) for t in (ticket, *children)) if d]
    return Facts(category=category(ticket, ctx), is_epic=bool(children), n_children=len(children),
                 n_done=cats.count(st.DONE), n_blocked=cats.count(st.BLOCKED), n_in_progress=cats.count(st.IN_PROGRESS),
                 due=from_iso(ticket.get("due")), updated=max(stamps) if stamps else None,
                 created=from_iso(ticket.get("created")),
                 blocked_label=any(st.normalize(x) in st.BLOCKED_LABELS for x in ticket.get("labels") or ()),
                 assignee=ticket.get("assignee"))


def fired(reported: str, owner: str | None, f: Facts, ctx: Ctx) -> list[str]:
    checks = _epic_checks(reported, f, ctx) if f.is_epic else _ticket_checks(reported, f, ctx)
    checks["D11"] = (reported in (st.ON_TRACK, st.DONE) and bool(owner) and f.assignee is not None
                     and st.normalize(f.assignee) in ("", "unassigned", "none"))
    return [rule for rule in RULES if checks.get(rule)]


def lead(rules: list[str]) -> str:
    return min(rules, key=lambda r: (RISK_ORDER[RULES[r][1]], int(r[1:])))


def _ticket_checks(reported: str, f: Facts, ctx: Ctx) -> dict[str, bool]:
    c, on_track = f.category, reported == st.ON_TRACK
    return {
        "D1": reported == st.DONE and c in (st.TODO, st.IN_PROGRESS, st.BLOCKED),
        "D2": on_track and c == st.BLOCKED,
        "D3": on_track and c == st.TODO and (_due_soon_or_past(f, ctx) or _older_than(f.created, OLD_TICKET_DAYS, ctx)),
        "D4": on_track and _overdue(f, ctx) and c not in (st.DONE, st.CANCELLED),
        "D5": on_track and c == st.IN_PROGRESS and _older_than(f.updated, ctx.stale_days, ctx),
        "D7": (reported == st.BLOCKED and c == st.IN_PROGRESS and not f.blocked_label
               and _within(f.updated, RECENT_DAYS, ctx)),
        "D8": reported == st.BLOCKED and c == st.DONE,
        "D9": reported == st.NOT_STARTED and f.started,
        "D10": (reported == st.AT_RISK and c == st.TODO and f.due is None
                and _older_than(f.updated, ctx.stale_days, ctx)),
    }


def _epic_checks(reported: str, f: Facts, ctx: Ctx) -> dict[str, bool]:
    on_track, untouched = reported == st.ON_TRACK, not f.started
    moving = f.n_in_progress > 0 or f.category == st.IN_PROGRESS
    return {
        "D1": reported == st.DONE and f.pct_done < EPIC_DONE_PCT,
        "D2": on_track and (f.n_blocked >= 1 or f.category == st.BLOCKED),
        "D3": on_track and untouched and (_due_soon_or_past(f, ctx) or _older_than(f.created, OLD_TICKET_DAYS, ctx)),
        "D4": on_track and _overdue(f, ctx) and f.pct_done < 1,
        "D5": on_track and moving and _older_than(f.updated, ctx.stale_days, ctx),
        "D6": (reported in (st.ON_TRACK, st.DONE) and f.pct_done < EPIC_BEHIND_PCT and f.due is not None
               and ctx.as_of <= f.due <= ctx.as_of + timedelta(days=ctx.soon_days)),
        "D7": (reported == st.BLOCKED and f.n_blocked == 0 and f.category != st.BLOCKED and moving
               and _within(f.updated, RECENT_DAYS, ctx)),
        "D8": reported == st.BLOCKED and f.pct_done == 1,
        "D9": reported == st.NOT_STARTED and f.started,
        "D10": reported == st.AT_RISK and untouched and f.due is None and _older_than(f.updated, ctx.stale_days, ctx),
    }


def _overdue(f: Facts, ctx: Ctx) -> bool:
    return f.due is not None and f.due < ctx.as_of


def _due_soon_or_past(f: Facts, ctx: Ctx) -> bool:
    return f.due is not None and f.due <= ctx.as_of + timedelta(days=ctx.soon_days)


def _older_than(day: date | None, days: int, ctx: Ctx) -> bool:
    return day is not None and (ctx.as_of - day).days > days


def _within(day: date | None, days: int, ctx: Ctx) -> bool:
    return day is not None and 0 <= (ctx.as_of - day).days <= days
