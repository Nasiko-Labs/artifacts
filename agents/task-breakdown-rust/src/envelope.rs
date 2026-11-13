//! Pure: the output envelope (the data part, and the fenced json block when asked). Every number, the
//! confidence and needs_human are computed here in code, never taken from the model.

use serde_json::{Map, Value, json};

use crate::metrics::{self, Metrics};
use crate::models::Plan;
use crate::planfile;

pub const HIGH_MAX_ASSUMPTIONS: usize = 2;

pub fn confidence(plan: &Plan) -> &'static str {
    if plan.cycle_broken || plan.vague_used || plan.clamped {
        "low"
    } else if plan.assumptions.len() > HIGH_MAX_ASSUMPTIONS
        || plan.code_fixes > 0
        || plan.chunks > 1
    {
        "medium"
    } else {
        "high"
    }
}

pub fn needs_human(plan: &Plan) -> bool {
    confidence(plan) == "low" || plan.tasks.iter().any(|t| t.size == "XL") || plan.policy_missing
}

pub fn summary(plan: &Plan, m: &Metrics) -> String {
    format!(
        "{} tasks in {} phases; critical path {} units across {} tasks.",
        plan.tasks.len(),
        plan.phases.len(),
        m.critical_units,
        m.critical.len()
    )
}

fn to_value<T: serde::Serialize>(item: &T) -> Value {
    serde_json::to_value(item).unwrap_or(Value::Null)
}

pub fn build(plan: &Plan) -> Value {
    let m = metrics::compute(plan);
    let items: Vec<Value> = plan
        .tasks
        .iter()
        .map(|t| {
            let mut item = match to_value(t) {
                Value::Object(map) => map,
                _ => Map::new(),
            };
            item.insert(
                "wave".into(),
                json!(m.wave_of.get(&t.id).copied().unwrap_or(0)),
            );
            item.insert("on_critical_path".into(), json!(m.critical.contains(&t.id)));
            item.insert("units".into(), json!(m.units_of(&t.id)));
            Value::Object(item)
        })
        .collect();
    let phases: Vec<Value> = plan
        .phases
        .iter()
        .map(|p| {
            let (count, units) = m.phase_count(p.number);
            let done = plan
                .tasks
                .iter()
                .filter(|t| t.phase == p.number)
                .all(|t| t.is_done());
            json!({"number": p.number, "name": p.name, "task_count": count, "units": units,
                   "status": if done { "done" } else { "pending" }})
        })
        .collect();
    let mut env = json!({
        "summary": summary(plan, &m), "items": items, "confidence": confidence(plan),
        "needs_human": needs_human(plan), "goal": plan.goal, "constraints": plan.constraints,
        "deadline": if plan.deadline.is_empty() { Value::Null } else { json!(plan.deadline) },
        "phases": phases, "critical_path": m.critical, "critical_units": m.critical_units,
        "total_units": m.total_units, "critical_share_percent": m.critical_share(), "waves": m.waves,
        "can_start_now": m.can_start(plan), "assumptions": to_value(&plan.assumptions),
        "risks": to_value(&plan.risks), "notes": plan.notes,
        "units": "relative effort units (S=1, M=2, L=3, XL=5), not days",
        "plan_md": planfile::render(plan, &m),
    });
    if let Some(capacity) = plan.capacity_units {
        env["fit"] = json!({"timebox": plan.timebox_label, "capacity_units": capacity, "basis": plan.fit_basis,
                            "deferred": to_value(&plan.deferred)});
    }
    if let Some(people) = plan.team_size.filter(|n| *n != 0) {
        let (lanes, makespan) = metrics::team_lanes(plan, people);
        env["lanes"] = json!({"people": people, "lanes": lanes, "makespan_units": makespan});
    }
    env
}
