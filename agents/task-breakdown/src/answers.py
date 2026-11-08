"""Pure: the text of each code-answered follow-up (see queries.py)."""

from __future__ import annotations

import metrics as metrics_mod
import planfile
import prompts
import render
from envelope import build
from models import Plan
from queries import Command
from taskgraph import dependents


def answer(plan: Plan, command: Command, wants_json: bool = False) -> tuple[str, dict | None]:
    """(reply, data part) for a follow-up on `plan` (already changed by `queries.with_change`)."""
    env = build(plan)
    m = metrics_mod.compute(plan)
    kind = command.kind
    if kind == "tracker":
        return prompts.TRACKER, None
    if kind == "json" or wants_json:
        return render.render_json(env), env
    if kind == "file":
        return render.render(plan, env), env
    if kind == "progress":
        return planfile.fenced(planfile.PROGRESS_TEMPLATE, "markdown progress.md"), None
    if kind in ("dependents", "task", "done") and not plan.task(command.task):
        return f"There's no {command.task} in this plan; it has {', '.join(t.id for t in plan.tasks)}.", None
    text = _TEXT[kind](plan, command, m, env)
    return text, (env if kind in ("team", "done", "rate") else None)


def _critical(plan: Plan, command: Command, m, env) -> str:
    return (f"**Critical path:** {' -> '.join(m.critical)}: {m.critical_units} of {m.total_units} units "
            f"({m.critical_share}% of the effort). More people shorten the plan only down to that chain. "
            f"{render.UNITS_NOTE}")


def _start(plan: Plan, command: Command, m, env) -> str:
    waves = "\n".join(f"- Wave {i + 1}: {', '.join(w)}" for i, w in enumerate(m.waves))
    return f"**Can start now:** {', '.join(m.can_start(plan)) or 'none'}\n\nParallel waves (tasks in a wave share " \
           f"no dependency):\n{waves}"


def _dependents(plan: Plan, command: Command, m, env) -> str:
    direct, everything = dependents(metrics_mod.deps_of(plan), command.task)
    if not everything:
        return f"Nothing depends on {command.task}."
    later = [t for t in everything if t not in direct]
    tail = f" Further downstream: {', '.join(later)}." if later else ""
    return f"Directly waiting on {command.task}: {', '.join(direct)}.{tail}"


def _phase_count(plan: Plan, command: Command, m, env) -> str:
    if command.number not in m.phase_counts:
        return f"There's no phase {command.number}; this plan has {len(plan.phases)} phases."
    count, units = m.phase_counts[command.number]
    name = next(p.name for p in plan.phases if p.number == command.number)
    return f"Phase {command.number} ({name}) has {count} tasks, {units} units."


def _count(plan: Plan, command: Command, m, env) -> str:
    rows = ", ".join(f"phase {n}: {c}" for n, (c, _) in m.phase_counts.items())
    return f"{len(plan.tasks)} tasks, {m.total_units} units in all ({rows})."


def _task(plan: Plan, command: Command, m, env) -> str:
    t = plan.task(command.task)
    criteria = "\n".join(f"- {a}" for a in t.acceptance)
    return (f"**{t.id} {t.title}** ({t.size}, {m.units[t.id]} units, phase {t.phase}, wave {m.wave_of[t.id]}"
            f"{', on the critical path' if t.id in m.critical else ''})\nOwner: {t.owner} · Needs: "
            f"{', '.join(t.depends_on) or 'none'} · Status: {t.status}\nDone when:\n{criteria}")


def _team(plan: Plan, command: Command, m, env) -> str:
    lanes = env["lanes"]
    return render.lanes_text(lanes["lanes"], lanes["makespan_units"]) + (
        f"\n\nThe critical path alone is {m.critical_units} units, so no team size finishes faster than that.")


def _rate(plan: Plan, command: Command, m, env) -> str:
    weeks = metrics_mod.weeks_needed(m.critical_units, command.number)
    makespan = env["lanes"]["makespan_units"] if "lanes" in env else m.total_units
    lane_weeks = metrics_mod.weeks_needed(makespan, command.number)
    deadline = f" Your deadline, as you wrote it: {plan.deadline}." if plan.deadline else ""
    return (f"At {command.number} units per week: the critical path ({m.critical_units} units) needs at least "
            f"{weeks} week(s); the whole plan ({makespan} units with your team's lanes) about {lane_weeks} "
            f"week(s).{deadline}")


def _done(plan: Plan, command: Command, m, env) -> str:
    return f"Marked {command.task} done. **Can start now:** {', '.join(m.can_start(plan)) or 'none'}."


_TEXT = {"critical": _critical, "start": _start, "dependents": _dependents, "phase_count": _phase_count,
         "count": _count, "task": _task, "team": _team, "rate": _rate, "done": _done}
