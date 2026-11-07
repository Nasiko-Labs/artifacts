"""Pure: who matters most, computed in code. Relationship, ask type, deadline and blocked weights sum
to an importance; age multiplies it; the nudge tier comes from the time since the last touch. The model
never sees or produces a number here."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from asks import Ask
from checks import parse_day, parse_time, recipients

REL_WEIGHTS = {"manager": 3.0, "external": 2.0, "direct": 1.5, "group": 1.0}
TYPE_WEIGHTS = {"decision": 2.0, "approval": 2.0, "review": 1.5, "input": 1.5, "deliverable": 1.5,
                "status": 1.0, "info": 0.5}
UNKNOWN_TYPE_WEIGHT = 1.0
BLOCKED_WEIGHT = 2.0
AGE_CAP_DAYS = 14
AGE_SCALE_DAYS = 7
HIGH_SCORE = 12
MEDIUM_SCORE = 6
RECENT_TOUCH_DAYS = 2
FIRM_TOUCH_DAYS = 5
FIRM_DEADLINE_DAYS = 3
MAX_LISTED = 15
_SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class Scored:
    ask: Ask
    relationship: str
    importance: float
    days_waiting: float
    days_since_touch: float
    days_to_deadline: int | None
    score: float
    label: str
    tier: str


@dataclass(frozen=True)
class Ranked:
    """What the user is shown: waiting then unclear, numbered from 1, at most MAX_LISTED."""

    items: tuple[Ask, ...]
    answered: int
    capped: int


def relationship(ask: Ask, me: Sequence[str], manager: str | None) -> str:
    if manager and _is_manager(ask, manager):
        return "manager"
    if ask.source == "email" and _is_external(ask, me):
        return "external"
    return "direct" if not ask.is_group and len(recipients(ask.to)) == 1 else "group"


def _is_manager(ask: Ask, manager: str) -> bool:
    wanted = manager.strip().casefold()
    name = ask.to_name.strip().casefold()
    return (wanted in recipients(ask.to) or wanted == name or name.startswith(wanted + " ")
            or bool(recipients(manager) & recipients(ask.to)))


def _is_external(ask: Ask, me: Sequence[str]) -> bool:
    mine = {_domain(m) for m in recipients(", ".join(me))} - {""}
    theirs = {_domain(r) for r in recipients(ask.to)} - {""}
    return bool(mine) and bool(theirs - mine)


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[1] if "@" in address else ""


def deadline_weight(days_to_deadline: int | None) -> float:
    if days_to_deadline is None:
        return 0.0
    if days_to_deadline <= 1:
        return 3.0
    if days_to_deadline <= 3:
        return 2.0
    return 1.0 if days_to_deadline <= 7 else 0.0


def tier_of(days_since_touch: float, days_to_deadline: int | None, blocked: bool) -> str:
    """recent (< 2 days since the last touch), else firm (≥ 5 days, deadline within 3 days or past,
    or blocked), else light."""
    if days_since_touch < RECENT_TOUCH_DAYS:
        return "recent"
    urgent = days_to_deadline is not None and days_to_deadline <= FIRM_DEADLINE_DAYS
    return "firm" if days_since_touch >= FIRM_TOUCH_DAYS or urgent or blocked else "light"


def label_of(score: float) -> str:
    return "High" if score >= HIGH_SCORE else "Medium" if score >= MEDIUM_SCORE else "Low"


def score(ask: Ask, now: datetime, me: Sequence[str], manager: str | None) -> Scored:
    asked = parse_time(ask.asked_at) or now
    touches = [t for t in (parse_time(n.at) for n in ask.later_nudges) if t is not None]
    last_touch = max([asked, *touches])
    days_waiting = _days(now, asked)
    deadline = parse_day(ask.deadline_date)
    to_deadline = (deadline - now.date()).days if deadline else None
    rel = relationship(ask, me, manager)
    blocked = bool(ask.blocking_quote)
    importance = (REL_WEIGHTS[rel] + TYPE_WEIGHTS.get(ask.ask_type, UNKNOWN_TYPE_WEIGHT)
                  + deadline_weight(to_deadline) + (BLOCKED_WEIGHT if blocked else 0.0))
    age_factor = 1 + min(days_waiting, AGE_CAP_DAYS) / AGE_SCALE_DAYS
    total = round(importance * age_factor, 2)
    since_touch = _days(now, last_touch)
    return Scored(ask=ask, relationship=rel, importance=importance, days_waiting=round(days_waiting, 1),
                  days_since_touch=since_touch, days_to_deadline=to_deadline, score=total, label=label_of(total),
                  tier=tier_of(since_touch, to_deadline, blocked))


def _days(now: datetime, then: datetime) -> float:
    return max(0.0, (now - then).total_seconds() / _SECONDS_PER_DAY)


def rank(asks: Sequence[Ask], now: datetime, me: Sequence[str], manager: str | None) -> Ranked:
    """Waiting by score (then days waiting, then oldest), then unclear the same way; renumbered 1..n."""
    def ordered(state: str) -> list[Ask]:
        scored = [score(a, now, me, manager) for a in asks if a.reply_state == state]
        scored.sort(key=lambda s: (-s.score, -s.days_waiting, parse_time(s.ask.asked_at) or _EPOCH_START))
        return [s.ask for s in scored]

    listed = [*ordered("waiting"), *ordered("unclear")]
    shown = tuple(replace(a, id=str(n)) for n, a in enumerate(listed[:MAX_LISTED], start=1))
    answered = sum(1 for a in asks if a.reply_state == "replied")
    return Ranked(items=shown, answered=answered, capped=max(0, len(listed) - MAX_LISTED))


_EPOCH_START = datetime.combine(date.min, datetime.min.time(), tzinfo=UTC)
