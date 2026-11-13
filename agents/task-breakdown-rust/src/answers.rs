//! Pure: the text of each code-answered follow-up (see queries.rs).

use serde_json::Value;

use crate::checks::deps_of;
use crate::envelope::build;
use crate::metrics::{self, Metrics};
use crate::models::Plan;
use crate::planfile;
use crate::prompts;
use crate::queries::Command;
use crate::render;
use crate::taskgraph::dependents;

fn list_or(items: &[String], empty: &str) -> String {
    if items.is_empty() {
        empty.to_string()
    } else {
        items.join(", ")
    }
}

/// (reply, data part) for a follow-up on `plan` (already changed by `queries::with_change`).
pub fn answer(plan: &Plan, command: &Command, wants_json: bool) -> (String, Option<Value>) {
    let env = build(plan);
    let m = metrics::compute(plan);
    let kind = command.kind;
    if kind == "tracker" {
        return (prompts::TRACKER.into(), None);
    }
    if kind == "json" || wants_json {
        return (render::render_json(&env), Some(env));
    }
    if kind == "file" {
        return (render::render(plan, &env, &[]), Some(env));
    }
    if kind == "progress" {
        return (
            planfile::fenced(planfile::PROGRESS_TEMPLATE, "markdown progress.md"),
            None,
        );
    }
    if matches!(kind, "dependents" | "task" | "done") && plan.task(&command.task).is_none() {
        let ids: Vec<String> = plan.tasks.iter().map(|t| t.id.clone()).collect();
        return (
            format!(
                "There's no {} in this plan; it has {}.",
                command.task,
                ids.join(", ")
            ),
            None,
        );
    }
    let text = match kind {
        "critical" => critical(&m),
        "start" => start(plan, &m),
        "dependents" => waiting_on(plan, command),
        "phase_count" => phase_count(plan, command, &m),
        "count" => count(plan, &m),
        "task" => one_task(plan, command, &m),
        "team" => team(&env, &m),
        "rate" => rate(plan, command, &m, &env),
        "done" => format!(
            "Marked {} done. **Can start now:** {}.",
            command.task,
            list_or(&m.can_start(plan), "none")
        ),
        _ => render::render(plan, &env, &[]),
    };
    let data = matches!(kind, "team" | "done" | "rate").then_some(env);
    (text, data)
}

fn critical(m: &Metrics) -> String {
    format!(
        "**Critical path:** {}: {} of {} units ({}% of the effort). More people shorten the plan only down to that chain. {}",
        m.critical.join(" -> "),
        m.critical_units,
        m.total_units,
        m.critical_share(),
        render::UNITS_NOTE
    )
}

fn start(plan: &Plan, m: &Metrics) -> String {
    let waves: Vec<String> = m
        .waves
        .iter()
        .enumerate()
        .map(|(i, w)| format!("- Wave {}: {}", i + 1, w.join(", ")))
        .collect();
    format!(
        "**Can start now:** {}\n\nParallel waves (tasks in a wave share no dependency):\n{}",
        list_or(&m.can_start(plan), "none"),
        waves.join("\n")
    )
}

fn waiting_on(plan: &Plan, command: &Command) -> String {
    let (direct, everything) = dependents(&deps_of(&plan.tasks), &command.task);
    if everything.is_empty() {
        return format!("Nothing depends on {}.", command.task);
    }
    let later: Vec<String> = everything
        .into_iter()
        .filter(|t| !direct.contains(t))
        .collect();
    let tail = if later.is_empty() {
        String::new()
    } else {
        format!(" Further downstream: {}.", later.join(", "))
    };
    format!(
        "Directly waiting on {}: {}.{tail}",
        command.task,
        direct.join(", ")
    )
}

fn phase_count(plan: &Plan, command: &Command, m: &Metrics) -> String {
    let Some((count, units)) = m.phase_counts.get(&command.number).copied() else {
        return format!(
            "There's no phase {}; this plan has {} phases.",
            command.number,
            plan.phases.len()
        );
    };
    let name = plan
        .phases
        .iter()
        .find(|p| p.number == command.number)
        .map_or("", |p| p.name.as_str());
    format!(
        "Phase {} ({name}) has {count} tasks, {units} units.",
        command.number
    )
}

fn count(plan: &Plan, m: &Metrics) -> String {
    let rows: Vec<String> = plan
        .phases
        .iter()
        .filter_map(|p| {
            m.phase_counts
                .get(&p.number)
                .map(|(c, _)| format!("phase {}: {c}", p.number))
        })
        .collect();
    format!(
        "{} tasks, {} units in all ({}).",
        plan.tasks.len(),
        m.total_units,
        rows.join(", ")
    )
}

fn one_task(plan: &Plan, command: &Command, m: &Metrics) -> String {
    let Some(t) = plan.task(&command.task) else {
        return String::new();
    };
    let criteria: Vec<String> = t.acceptance.iter().map(|a| format!("- {a}")).collect();
    let on_path = if m.critical.contains(&t.id) {
        ", on the critical path"
    } else {
        ""
    };
    format!(
        "**{} {}** ({}, {} units, phase {}, wave {}{on_path})\nOwner: {} · Needs: {} · Status: {}\nDone when:\n{}",
        t.id,
        t.title,
        t.size,
        m.units_of(&t.id),
        t.phase,
        m.wave_of.get(&t.id).copied().unwrap_or(0),
        t.owner,
        list_or(&t.depends_on, "none"),
        t.status,
        criteria.join("\n")
    )
}

fn team(env: &Value, m: &Metrics) -> String {
    let lanes = &env["lanes"];
    format!(
        "{}\n\nThe critical path alone is {} units, so no team size finishes faster than that.",
        render::lanes_text(
            &render::lanes_of(&lanes["lanes"]),
            lanes["makespan_units"].as_i64().unwrap_or(0)
        ),
        m.critical_units
    )
}

fn rate(plan: &Plan, command: &Command, m: &Metrics, env: &Value) -> String {
    let weeks = metrics::weeks_needed(m.critical_units, command.number);
    let makespan = env
        .get("lanes")
        .and_then(|l| l["makespan_units"].as_i64())
        .unwrap_or(m.total_units);
    let lane_weeks = metrics::weeks_needed(makespan, command.number);
    let deadline = if plan.deadline.is_empty() {
        String::new()
    } else {
        format!(" Your deadline, as you wrote it: {}.", plan.deadline)
    };
    format!(
        "At {} units per week: the critical path ({} units) needs at least {weeks} week(s); the whole plan ({makespan} units with your team's lanes) about {lane_weeks} week(s).{deadline}",
        command.number, m.critical_units
    )
}
