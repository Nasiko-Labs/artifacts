//! Pure: the markdown reply, rendered by code from the checked plan and its envelope. Markdown only: no
//! HTML, no images, no links.

use serde_json::Value;

use crate::metrics;
use crate::models::{POLICY_MARK, Plan};
use crate::planfile::{self, PLAN_FILE_LANG};
use crate::taskgraph::units_of;
use crate::util::{collapse_ws, take_chars};

pub const FOOTER: &str = "_Draft only: nothing was created anywhere. Say \"split T4\", \"we are 2 people\", or \"what depends on T7?\"._";
pub const UNITS_NOTE: &str = "Sizes are relative units (S=1, M=2, L=3, XL=5), not days.";

pub fn cell(text: &str) -> String {
    collapse_ws(text).replace('|', "\\|")
}

/// `json.dumps(env, indent=2, ensure_ascii=False)` in a json fence.
pub fn render_json(env: &Value) -> String {
    let body = serde_json::to_string_pretty(env).unwrap_or_else(|_| "{}".into());
    format!("```json\n{body}\n```")
}

pub fn render(plan: &Plan, env: &Value, lead: &[&str]) -> String {
    let m = metrics::compute(plan);
    let goal = cell(&plan.goal);
    let head = format!(
        "**Plan: {}**  ·  {} tasks · {} phases · critical path {} units ({} tasks)",
        take_chars(&goal, 160),
        plan.tasks.len(),
        plan.phases.len(),
        m.critical_units,
        m.critical.len()
    );
    let mut out: Vec<String> = lead.iter().map(|l| l.to_string()).collect();
    out.extend([head, String::new()]);
    for phase in &plan.phases {
        let (count, units) = m.phase_count(phase.number);
        out.extend([
            format!(
                "**Phase {}: {}** ({count} tasks, {units} units)",
                phase.number,
                cell(&phase.name)
            ),
            String::new(),
            "| ID | Task | Size | Needs | Owner | Done when |".into(),
            "|---|---|---|---|---|---|".into(),
        ]);
        for t in plan.tasks.iter().filter(|t| t.phase == phase.number) {
            let shown = format!(
                "{}{}",
                cell(&t.title),
                if t.is_policy() { POLICY_MARK } else { "" }
            );
            let title = if t.is_done() {
                format!("~~{shown}~~ (done)")
            } else {
                shown
            };
            let needs = if t.depends_on.is_empty() {
                "none".into()
            } else {
                t.depends_on.join(", ")
            };
            out.push(format!(
                "| {} | {title} | {} | {needs} | {} | {} |",
                t.id,
                t.size,
                cell(&t.owner),
                cell(&t.acceptance.join("; "))
            ));
        }
        out.push(String::new());
    }
    let can_start = m.can_start(plan);
    let path = format!(
        "**Critical path:** {} ({} of {} units, {}% of the effort: more people help only up to that point). {UNITS_NOTE}",
        m.critical.join(" -> "),
        m.critical_units,
        m.total_units,
        m.critical_share()
    );
    out.extend([
        format!(
            "**Can start now:** {}  ",
            if can_start.is_empty() {
                "none".into()
            } else {
                can_start.join(", ")
            }
        ),
        path,
        String::new(),
    ]);
    out.extend(extras(plan, env));
    let plan_md = env["plan_md"].as_str().unwrap_or("");
    out.extend([
        planfile::fenced(plan_md, PLAN_FILE_LANG),
        String::new(),
        FOOTER.into(),
    ]);
    out.join("\n")
}

fn extras(plan: &Plan, env: &Value) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    if !plan.constraints.is_empty() {
        let items: Vec<String> = plan.constraints.iter().map(|c| cell(c)).collect();
        out.push(format!("**Constraints:** {}", items.join(" · ")));
    }
    if plan.capacity_units.is_some() {
        out.push(fitted(plan));
    } else if !plan.deadline.is_empty() {
        out.push(format!(
            "**Deadline:** {}, as you wrote it. I have not converted units to dates; tell me how many units your team does per week and I will compare.",
            cell(&plan.deadline)
        ));
    }
    if !plan.assumptions.is_empty() {
        let items: Vec<String> = plan
            .assumptions
            .iter()
            .map(|a| format!("{} {} (\"{}\")", a.id, cell(&a.text), cell(&a.based_on)))
            .collect();
        out.push(format!("**Assumptions:** {}", items.join(" · ")));
    }
    if !plan.risks.is_empty() {
        let items: Vec<String> = plan
            .risks
            .iter()
            .map(|r| format!("{} ({}) {}", r.id, r.task, cell(&r.text)))
            .collect();
        out.push(format!("**Risks:** {}", items.join(" · ")));
    }
    if let Some(lanes) = env.get("lanes") {
        out.push(lanes_text(
            &lanes_of(&lanes["lanes"]),
            lanes["makespan_units"].as_i64().unwrap_or(0),
        ));
    }
    if !plan.notes.is_empty() {
        let items: Vec<String> = plan.notes.iter().map(|n| cell(n)).collect();
        out.push(format!("**Notes:** {}", items.join(" · ")));
    }
    out.into_iter()
        .flat_map(|line| [line, String::new()])
        .collect()
}

pub fn lanes_of(value: &Value) -> Vec<Vec<String>> {
    value
        .as_array()
        .map(|lanes| {
            lanes
                .iter()
                .map(|lane| {
                    lane.as_array()
                        .map(|ids| {
                            ids.iter()
                                .filter_map(Value::as_str)
                                .map(str::to_string)
                                .collect()
                        })
                        .unwrap_or_default()
                })
                .collect()
        })
        .unwrap_or_default()
}

fn units_sum(tasks: &[crate::models::Task]) -> i64 {
    tasks.iter().map(|t| units_of(&t.size).unwrap_or(0)).sum()
}

fn fitted(plan: &Plan) -> String {
    let label = cell(&plan.timebox_label);
    let head = format!(
        "**Fitted to {label}:** {} of {} units ({}).",
        units_sum(&plan.tasks),
        plan.capacity_units.unwrap_or(0),
        cell(&plan.fit_basis)
    );
    if plan.deferred.is_empty() {
        return format!("{head} Everything fits; nothing was deferred.");
    }
    let rows: Vec<String> = plan
        .deferred
        .iter()
        .map(|t| {
            let needs = if t.depends_on.is_empty() {
                String::new()
            } else {
                format!(", needs {}", t.depends_on.join(", "))
            };
            format!("- {} {} ({}{needs})", t.id, cell(&t.title), t.size)
        })
        .collect();
    format!(
        "{head}\n\n**Deferred to fit {label}** ({} tasks, {} units; the lowest priority: latest phase and off the critical path first):\n{}",
        plan.deferred.len(),
        units_sum(&plan.deferred),
        rows.join("\n")
    )
}

pub fn lanes_text(lanes: &[Vec<String>], makespan: i64) -> String {
    let rows: Vec<String> = lanes
        .iter()
        .enumerate()
        .map(|(i, lane)| {
            format!(
                "- Person {}: {}",
                i + 1,
                if lane.is_empty() {
                    "(free)".into()
                } else {
                    lane.join(", ")
                }
            )
        })
        .collect();
    let who = if lanes.len() == 1 { "person" } else { "people" };
    format!(
        "**Lanes for {} {who}** (finishes in {makespan} relative units, not days):\n{}",
        lanes.len(),
        rows.join("\n")
    )
}
