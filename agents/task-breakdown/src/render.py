"""Pure: the markdown reply, rendered by code from the checked plan and its envelope. Markdown only: no
HTML, no images, no links."""

from __future__ import annotations

import json

import metrics as metrics_mod
import planfile
from models import Plan
from taskgraph import UNITS

FOOTER = '_Draft only: nothing was created anywhere. Say "split T4", "we are 2 people", or "what depends on T7?"._'
UNITS_NOTE = "Sizes are relative units (S=1, M=2, L=3, XL=5), not days."


def cell(text: str) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


def render_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"


def render(plan: Plan, env: dict, lead: tuple[str, ...] = ()) -> str:
    m = metrics_mod.compute(plan)
    head = (f"**Plan: {cell(plan.goal)[:160]}**  ·  {len(plan.tasks)} tasks · {len(plan.phases)} phases · "
            f"critical path {m.critical_units} units ({len(m.critical)} tasks)")
    out = [*lead, head, ""]
    for phase in plan.phases:
        count, units = m.phase_counts[phase.number]
        out += [f"**Phase {phase.number}: {cell(phase.name)}** ({count} tasks, {units} units)", "",
                "| ID | Task | Size | Needs | Owner | Done when |", "|---|---|---|---|---|---|"]
        for t in (t for t in plan.tasks if t.phase == phase.number):
            title = f"~~{cell(t.title)}~~ (done)" if t.status == "done" else cell(t.title)
            out.append(f"| {t.id} | {title} | {t.size} | {', '.join(t.depends_on) or 'none'} | {cell(t.owner)} | "
                       f"{cell('; '.join(t.acceptance))} |")
        out.append("")
    path = (f"**Critical path:** {' -> '.join(m.critical)} ({m.critical_units} of {m.total_units} units, "
            f"{m.critical_share}% of the effort: more people help only up to that point). {UNITS_NOTE}")
    out += [f"**Can start now:** {', '.join(m.can_start(plan)) or 'none'}  ", path, ""]
    out += _extras(plan, env)
    out += [planfile.fenced(env["plan_md"]), "", FOOTER]
    return "\n".join(out)


def _extras(plan: Plan, env: dict) -> list[str]:
    out: list[str] = []
    if plan.constraints:
        out.append("**Constraints:** " + " · ".join(cell(c) for c in plan.constraints))
    if plan.capacity_units is not None:
        out.append(_fitted(plan))
    elif plan.deadline:
        out.append(f"**Deadline:** {cell(plan.deadline)}, as you wrote it. I have not converted units to dates; "
                   "tell me how many units your team does per week and I will compare.")
    if plan.assumptions:
        out.append("**Assumptions:** " + " · ".join(f"{a.id} {cell(a.text)} (\"{cell(a.based_on)}\")"
                                                    for a in plan.assumptions))
    if plan.risks:
        out.append("**Risks:** " + " · ".join(f"{r.id} ({r.task}) {cell(r.text)}" for r in plan.risks))
    if "lanes" in env:
        out.append(lanes_text(env["lanes"]["lanes"], env["lanes"]["makespan_units"]))
    if plan.notes:
        out.append("**Notes:** " + " · ".join(cell(n) for n in plan.notes))
    return [line for x in out for line in (x, "")]


def _fitted(plan: Plan) -> str:
    kept = sum(UNITS[t.size] for t in plan.tasks)
    head = (f"**Fitted to {cell(plan.timebox_label)}:** {kept} of {plan.capacity_units} units "
            f"({cell(plan.fit_basis)}).")
    if not plan.deferred:
        return head + " Everything fits; nothing was deferred."
    units = sum(UNITS[t.size] for t in plan.deferred)
    rows = "\n".join(f"- {t.id} {cell(t.title)} ({t.size}{', needs ' + ', '.join(t.depends_on) if t.depends_on else ''})"
                     for t in plan.deferred)
    return (f"{head}\n\n**Deferred to fit {cell(plan.timebox_label)}** ({len(plan.deferred)} tasks, {units} units; "
            f"the lowest priority: latest phase and off the critical path first):\n{rows}")


def lanes_text(lanes: list[list[str]], makespan: int) -> str:
    rows = "\n".join(f"- Person {i + 1}: {', '.join(lane) or '(free)'}" for i, lane in enumerate(lanes))
    return (f"**Lanes for {len(lanes)} {'person' if len(lanes) == 1 else 'people'}** (finishes in {makespan} "
            f"relative units, not days):\n{rows}")
