"""Pure: the planning-with-files layout rendered from the checked plan. `task_plan.md` holds the goal,
constraints, phases with status checkboxes and the tasks; the findings file maps onto "Assumptions & open
questions"; the progress file is an empty template (no dates are ever written by the agent)."""

from __future__ import annotations

from metrics import Metrics
from models import POLICY_MARK, Plan

PROGRESS_TEMPLATE = (
    "# Progress log\n\n"
    "Add one dated line per change, newest last. Paste this back with \"update the plan\" to re-plan.\n\n"
    "- [date] T? started / done / blocked: <what happened>\n"
)


def _box(done: bool) -> str:
    return "[x]" if done else "[ ]"


def render(plan: Plan, m: Metrics) -> str:
    lines = [f"# Task plan: {_one_line(plan.goal)}", "", "## Goal", _one_line(plan.goal), "", "## Constraints"]
    lines += [f"- {c}" for c in plan.constraints] or ["- none stated"]
    lines += ["", "## Phases"]
    for phase in plan.phases:
        tasks = [t for t in plan.tasks if t.phase == phase.number]
        done = all(t.status == "done" for t in tasks)
        lines += ["", f"### Phase {phase.number}: {phase.name}  {_box(done)} {'done' if done else 'pending'}"]
        for t in tasks:
            needs = ", ".join(t.depends_on) or "none"
            title = t.title + (POLICY_MARK if t.source == "policy" else "")
            lines.append(f"- {_box(t.status == 'done')} {t.id} {title} ({t.size}) — owner: {t.owner} — "
                         f"needs: {needs} — done when: {'; '.join(t.acceptance)}")
    lines += ["", "## Assumptions & open questions"]
    lines += [f"- {a.id} {a.text} (from: \"{a.based_on}\")" for a in plan.assumptions] or ["- none"]
    lines += [f"- {r.id} risk on {r.task}: {r.text}" for r in plan.risks]
    chain = " -> ".join(m.critical) or "none"
    path = f"{chain} ({m.critical_units} units of {m.total_units}; relative units, not days)"
    lines += ["", "## Critical path", path, "", "## Progress log", "(empty; add dated lines as work happens)"]
    return "\n".join(lines)


def _one_line(text: str) -> str:
    return " ".join((text or "").split())[:200]


def fenced(body: str, lang: str = "markdown task_plan.md") -> str:
    fence = "````" if "```" in body else "```"
    return f"{fence}{lang}\n{body}\n{fence}"
