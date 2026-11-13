//! The plan. Every change makes a new copy (struct update syntax); nothing is mutated in place where it
//! is shared. Never stored: a follow-up reads the plan back from the platform history slice (recall.rs).

use serde::Serialize;

pub const OWNER_PLACEHOLDER: &str = "[owner?]";
/// Shown after a policy task's title, so the source survives a re-read.
pub const POLICY_MARK: &str = " (org rule)";
pub const SOURCES: &[&str] = &["user", "derived", "policy"];

/// One task. The model's draft uses the same shape with its own ids and unchecked sizes; a task in a
/// `Plan` always has a size in S/M/L/XL and a source in `SOURCES`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Task {
    pub id: String,
    pub phase: i64,
    pub title: String,
    pub size: String,
    pub depends_on: Vec<String>,
    pub acceptance: Vec<String>,
    pub owner: String,
    pub source: String,
    pub status: String,
}

impl Task {
    pub fn is_done(&self) -> bool {
        self.status == "done"
    }

    pub fn is_policy(&self) -> bool {
        self.source == "policy"
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Phase {
    pub number: i64,
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Risk {
    pub id: String,
    pub task: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Assumption {
    pub id: String,
    pub text: String,
    pub based_on: String,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Plan {
    pub goal: String,
    pub constraints: Vec<String>,
    pub phases: Vec<Phase>,
    pub tasks: Vec<Task>,
    pub risks: Vec<Risk>,
    pub assumptions: Vec<Assumption>,
    /// What code changed or could not fix, shown to the user.
    pub notes: Vec<String>,
    /// As the user typed it, never converted.
    pub deadline: String,
    pub team_size: Option<i64>,
    /// Units per week, only when the user said so.
    pub rate: Option<i64>,
    /// The number the next new task gets (ids are never reused).
    pub next_id: i64,
    pub code_fixes: usize,
    pub cycle_broken: bool,
    pub clamped: bool,
    pub chunks: usize,
    pub vague_used: bool,
    pub policy_missing: bool,
    pub granularity: String,
    /// The user's own words for this plan: grounding for assumptions/owners.
    pub source: String,
    /// Cut by fit.rs to meet a timebox, shown under "Deferred to fit ...".
    pub deferred: Vec<Task>,
    /// The timebox's capacity in units (fit.rs), when one was fitted.
    pub capacity_units: Option<i64>,
    /// "2 weeks"
    pub timebox_label: String,
    /// How the capacity was worked out, as shown to the user.
    pub fit_basis: String,
}

impl Plan {
    pub fn task(&self, task_id: &str) -> Option<&Task> {
        self.tasks.iter().find(|t| t.id == task_id)
    }
}
