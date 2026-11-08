"""Pure: the output envelope (the DataPart, and the fenced json block when asked). Every number, the
confidence and needs_human are computed here in code, never taken from the model."""

from __future__ import annotations

import metrics as metrics_mod
import planfile
from models import Plan

HIGH_MAX_ASSUMPTIONS = 2


def confidence(plan: Plan) -> str:
    if plan.cycle_broken or plan.vague_used or plan.clamped:
        return "low"
    if len(plan.assumptions) > HIGH_MAX_ASSUMPTIONS or plan.code_fixes or plan.chunks > 1:
        return "medium"
    return "high"


def needs_human(plan: Plan) -> bool:
    return confidence(plan) == "low" or any(t.size == "XL" for t in plan.tasks) or plan.policy_missing


def summary(plan: Plan, m: metrics_mod.Metrics) -> str:
    return (f"{len(plan.tasks)} tasks in {len(plan.phases)} phases; critical path {m.critical_units} units "
            f"across {len(m.critical)} tasks.")


def build(plan: Plan) -> dict:
    m = metrics_mod.compute(plan)
    critical = set(m.critical)
    items = [{**t.model_dump(mode="json"), "wave": m.wave_of[t.id], "on_critical_path": t.id in critical,
              "units": m.units[t.id]} for t in plan.tasks]
    env = {
        "summary": summary(plan, m), "items": items, "confidence": confidence(plan), "needs_human": needs_human(plan),
        "goal": plan.goal, "constraints": list(plan.constraints), "deadline": plan.deadline or None,
        "phases": [{"number": p.number, "name": p.name, "task_count": m.phase_counts[p.number][0],
                    "units": m.phase_counts[p.number][1],
                    "status": "done" if all(t.status == "done" for t in plan.tasks if t.phase == p.number)
                    else "pending"} for p in plan.phases],
        "critical_path": m.critical, "critical_units": m.critical_units, "total_units": m.total_units,
        "critical_share_percent": m.critical_share, "waves": m.waves, "can_start_now": m.can_start(plan),
        "assumptions": [a.model_dump(mode="json") for a in plan.assumptions],
        "risks": [r.model_dump(mode="json") for r in plan.risks], "notes": list(plan.notes),
        "units": "relative effort units (S=1, M=2, L=3, XL=5), not days",
        "plan_md": planfile.render(plan, m),
    }
    if plan.team_size:
        lanes, makespan = metrics_mod.team_lanes(plan, plan.team_size)
        env["lanes"] = {"people": plan.team_size, "lanes": lanes, "makespan_units": makespan}
    return env
