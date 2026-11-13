"""Pure: fit a plan to a timebox ("a 2-week plan for one engineer"), in code, from the size labels.

Capacity = weeks x the user's stated rate (units per week), else weeks x DEFAULT_UNITS_PER_PERSON_WEEK x people,
and the reply says which. While the plan is over capacity, the lowest-priority task that nothing kept still
needs is deferred: the latest phase first, then off the critical path, then the largest, then the highest id.
Done and org-rule tasks are never deferred. The kept tasks are sequenced for the team by the lanes code."""

from __future__ import annotations

import math
import re

from inputs import NUMBER_WORDS
from models import Plan, Task
from taskgraph import UNITS, critical_path, id_key

DEFAULT_UNITS_PER_PERSON_WEEK = 5      # assumed (1 unit ~ a focused day) when the user gave no rate; stated in the reply
_WEEKS_PER = {"day": 1 / 5, "week": 1.0, "sprint": 2.0, "month": 4.0}
_NUM = r"(\d{1,3}|" + "|".join(NUMBER_WORDS) + r")"
_TIMEBOX = re.compile(rf"\b{_NUM}[- ]?(day|week|sprint|month)s?\b", re.IGNORECASE)


def timebox(text: str) -> tuple[float, str] | None:
    """(weeks, the timebox as a label like "2 weeks"), or None when the text holds no countable timebox."""
    match = _TIMEBOX.search(text or "")
    if not match:
        return None
    raw, unit = match.group(1).casefold(), match.group(2).casefold()
    count = NUMBER_WORDS.get(raw) or int(raw)
    if count <= 0:
        return None
    return count * _WEEKS_PER[unit], f"{count} {unit}{'' if count == 1 else 's'}"


def fit(plan: Plan) -> Plan:
    """The plan cut to its timebox (unchanged when it has none or already fits)."""
    box = timebox(plan.deadline)
    if box is None:
        return plan
    weeks, label = box
    people = plan.team_size or 1
    if plan.rate:
        weekly, basis = plan.rate, f"{label} at your {plan.rate} units per week"
    else:
        weekly = DEFAULT_UNITS_PER_PERSON_WEEK * people
        who = "1 person" if people == 1 else f"{people} people"
        basis = (f"{label} x {who} x {DEFAULT_UNITS_PER_PERSON_WEEK} units per person-week, assumed; say "
                 "\"4 units per week\" to change it")
    capacity = math.floor(weeks * weekly)
    kept, deferred = _cut(plan.tasks, capacity)
    ids = {t.id for t in kept}
    notes = plan.notes
    over = sum(UNITS[t.size] for t in kept) - capacity
    if over > 0:
        notes = (*notes, f"Still {over} units over {label}: the remaining tasks are needed by others or done.")
    return plan.model_copy(update={
        "tasks": kept, "deferred": (*plan.deferred, *deferred), "capacity_units": capacity, "timebox_label": label,
        "fit_basis": basis, "notes": notes,
        "phases": tuple(p for p in plan.phases if any(t.phase == p.number for t in kept)),
        "risks": tuple(r for r in plan.risks if r.task in ids)})


def _cut(tasks: tuple[Task, ...], capacity: int) -> tuple[tuple[Task, ...], tuple[Task, ...]]:
    units = {t.id: UNITS[t.size] for t in tasks}
    chain, _ = critical_path({t.id: t.depends_on for t in tasks}, units)
    critical = set(chain)
    kept, deferred = list(tasks), []
    while sum(units[t.id] for t in kept) > capacity:
        needed = {d for t in kept for d in t.depends_on}
        movable = [t for t in kept if t.id not in needed and t.status != "done" and t.source != "policy"]
        if not movable:
            break
        last = max(movable, key=lambda t: (t.phase, t.id not in critical, units[t.id], id_key(t.id)))
        kept.remove(last)
        deferred.append(last)
    return tuple(kept), tuple(sorted(deferred, key=lambda t: id_key(t.id)))
