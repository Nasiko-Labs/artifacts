//! Pure: the planning-with-files layout rendered from the checked plan. `task_plan.md` holds the goal,
//! constraints, phases with status checkboxes and the tasks; the findings file maps onto "Assumptions &
//! open questions"; the progress file is an empty template (no dates are ever written by the agent).

use crate::metrics::Metrics;
use crate::models::{POLICY_MARK, Plan};
use crate::util::{collapse_ws, take_chars};

pub const PROGRESS_TEMPLATE: &str = concat!(
    "# Progress log\n\n",
    "Add one dated line per change, newest last. Paste this back with \"update the plan\" to re-plan.\n\n",
    "- [date] T? started / done / blocked: <what happened>\n"
);

fn tick(done: bool) -> &'static str {
    if done { "[x]" } else { "[ ]" }
}

fn one_line(text: &str) -> String {
    take_chars(&collapse_ws(text), 200).to_string()
}

pub fn render(plan: &Plan, m: &Metrics) -> String {
    let goal = one_line(&plan.goal);
    let mut lines = vec![
        format!("# Task plan: {goal}"),
        String::new(),
        "## Goal".into(),
        goal.clone(),
        String::new(),
        "## Constraints".into(),
    ];
    if plan.constraints.is_empty() {
        lines.push("- none stated".into());
    }
    lines.extend(plan.constraints.iter().map(|c| format!("- {c}")));
    lines.extend([String::new(), "## Phases".into()]);
    for phase in &plan.phases {
        let tasks: Vec<_> = plan
            .tasks
            .iter()
            .filter(|t| t.phase == phase.number)
            .collect();
        let done = tasks.iter().all(|t| t.is_done());
        lines.push(String::new());
        lines.push(format!(
            "### Phase {}: {}  {} {}",
            phase.number,
            phase.name,
            tick(done),
            if done { "done" } else { "pending" }
        ));
        for t in tasks {
            let needs = if t.depends_on.is_empty() {
                "none".into()
            } else {
                t.depends_on.join(", ")
            };
            let mark = if t.is_policy() { POLICY_MARK } else { "" };
            lines.push(format!(
                "- {} {} {}{mark} ({}) — owner: {} — needs: {needs} — done when: {}",
                tick(t.is_done()),
                t.id,
                t.title,
                t.size,
                t.owner,
                t.acceptance.join("; ")
            ));
        }
    }
    lines.extend([String::new(), "## Assumptions & open questions".into()]);
    if plan.assumptions.is_empty() {
        lines.push("- none".into());
    }
    lines.extend(
        plan.assumptions
            .iter()
            .map(|a| format!("- {} {} (from: \"{}\")", a.id, a.text, a.based_on)),
    );
    lines.extend(
        plan.risks
            .iter()
            .map(|r| format!("- {} risk on {}: {}", r.id, r.task, r.text)),
    );
    let chain = if m.critical.is_empty() {
        "none".into()
    } else {
        m.critical.join(" -> ")
    };
    lines.extend([
        String::new(),
        "## Critical path".into(),
        format!(
            "{chain} ({} units of {}; relative units, not days)",
            m.critical_units, m.total_units
        ),
        String::new(),
        "## Progress log".into(),
        "(empty; add dated lines as work happens)".into(),
    ]);
    lines.join("\n")
}

pub fn fenced(body: &str, lang: &str) -> String {
    let fence = if body.contains("```") { "````" } else { "```" };
    format!("{fence}{lang}\n{body}\n{fence}")
}

pub const PLAN_FILE_LANG: &str = "markdown task_plan.md";
