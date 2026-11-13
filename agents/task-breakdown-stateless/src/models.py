"""The checked plan. Frozen: every change makes a new copy (`model_copy(update=...)`). Never stored: a
follow-up reads the plan back from the platform history slice (recall.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Size = Literal["S", "M", "L", "XL"]
Source = Literal["user", "derived", "policy"]
OWNER_PLACEHOLDER = "[owner?]"
POLICY_MARK = " (org rule)"  # shown after a policy task's title, so the source survives a re-read


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Task(_Frozen):
    id: str
    phase: int
    title: str
    size: Size
    depends_on: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    owner: str = OWNER_PLACEHOLDER
    source: Source = "derived"
    status: Literal["pending", "done"] = "pending"


class Phase(_Frozen):
    number: int
    name: str


class Risk(_Frozen):
    id: str
    task: str
    text: str


class Assumption(_Frozen):
    id: str
    text: str
    based_on: str


class Plan(_Frozen):
    goal: str
    constraints: tuple[str, ...] = ()
    phases: tuple[Phase, ...] = ()
    tasks: tuple[Task, ...] = ()
    risks: tuple[Risk, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    notes: tuple[str, ...] = ()       # what code changed or could not fix, shown to the user
    deadline: str = ""                # as the user typed it, never converted
    team_size: int | None = None
    rate: int | None = None           # units per week, only when the user said so
    next_id: int = 1                  # the number the next new task gets (ids are never reused)
    code_fixes: int = 0
    cycle_broken: bool = False
    clamped: bool = False
    chunks: int = 0
    vague_used: bool = False
    policy_missing: bool = False
    granularity: str = "normal"
    source: str = ""                  # the user's own words for this plan: grounding for assumptions/owners
    deferred: tuple[Task, ...] = ()   # cut by fit.py to meet a timebox, shown under "Deferred to fit ..."
    capacity_units: int | None = None # the timebox's capacity in units (fit.py), when one was fitted
    timebox_label: str = ""           # "2 weeks"
    fit_basis: str = ""               # how the capacity was worked out, as shown to the user

    def task(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)
