"""Pure: every number the reply shows, computed once from the checked plan."""

from __future__ import annotations

import math
from dataclasses import dataclass

from models import Plan
from taskgraph import UNITS, critical_path, lanes, waves


@dataclass(frozen=True)
class Metrics:
    units: dict[str, int]
    wave_of: dict[str, int]
    waves: list[list[str]]
    critical: list[str]
    critical_units: int
    total_units: int
    phase_counts: dict[int, tuple[int, int]]     # phase -> (tasks, units)

    @property
    def critical_share(self) -> int:
        """The critical path as a whole percent of all effort."""
        return round(100 * self.critical_units / self.total_units) if self.total_units else 0

    def can_start(self, plan: Plan) -> list[str]:
        """Pending tasks whose dependencies are all done (at creation: wave 1)."""
        done = {t.id for t in plan.tasks if t.status == "done"}
        return [t.id for t in plan.tasks if t.status != "done" and set(t.depends_on) <= done]


def deps_of(plan: Plan) -> dict[str, tuple[str, ...]]:
    return {t.id: t.depends_on for t in plan.tasks}


def compute(plan: Plan) -> Metrics:
    deps = deps_of(plan)
    units = {t.id: UNITS[t.size] for t in plan.tasks}
    wave_of = waves(deps)
    chain, crit = critical_path(deps, units)
    lists = [[n for n in wave_of if wave_of[n] == k] for k in range(1, max(wave_of.values(), default=0) + 1)]
    counts = {p.number: (sum(1 for t in plan.tasks if t.phase == p.number),
                         sum(units[t.id] for t in plan.tasks if t.phase == p.number)) for p in plan.phases}
    return Metrics(units, wave_of, [sorted(w, key=list(deps).index) for w in lists], chain, crit,
                   sum(units.values()), counts)


def team_lanes(plan: Plan, people: int) -> tuple[list[list[str]], int]:
    return lanes(deps_of(plan), {t.id: UNITS[t.size] for t in plan.tasks}, people)


def weeks_needed(critical_units: int, rate: int) -> int:
    """Weeks at least, at `rate` units per week: the critical path cannot be shortened by more people."""
    return math.ceil(critical_units / rate) if rate > 0 else 0
