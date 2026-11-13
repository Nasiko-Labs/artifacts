//! Pure: turn a (repaired or not) draft into the checked Plan: phases renumbered 1..k, task ids assigned in
//! code, code fixes applied, assumptions kept only when tied to the user's own words, risks tied to tasks,
//! constraints checked against the text, and the flags confidence is computed from.

use std::collections::{BTreeSet, HashMap};

use crate::checks::{Draft, MAX_ASSUMPTIONS, MAX_RISKS, policy_expects_task, two_verbs};
use crate::fixes;
use crate::grounding::{is_verbatim, mostly_grounded, norm};
use crate::models::{Assumption, Phase, Plan, Risk, Task};
use crate::taskgraph::id_key;
use crate::util::dedupe;

#[derive(Debug, Clone, PartialEq)]
pub struct Context {
    pub goal: String,
    /// The user's words: goal + spec (+ revision instructions).
    pub source: String,
    pub band: (usize, usize),
    pub policy: Vec<String>,
    /// A new plan: ids T1..Tn in code; a revision keeps its reconciled ids.
    pub renumber: bool,
    pub next_id: i64,
    pub known_constraints: Vec<String>,
    pub deadline: String,
    pub team_size: Option<i64>,
    pub rate: Option<i64>,
    pub chunks: usize,
    pub vague_used: bool,
    pub notes: Vec<String>,
}

impl Context {
    pub fn new(goal: &str, source: &str, band: (usize, usize)) -> Self {
        Self {
            goal: goal.to_string(),
            source: source.to_string(),
            band,
            policy: Vec::new(),
            renumber: true,
            next_id: 1,
            known_constraints: Vec::new(),
            deadline: String::new(),
            team_size: None,
            rate: None,
            chunks: 0,
            vague_used: false,
            notes: Vec::new(),
        }
    }
}

pub fn finalize(draft: &Draft, ctx: &Context) -> Plan {
    let (phases, tasks) = renumber_phases(draft);
    let (tasks, mapping) = assign_ids(tasks, ctx);
    let fixed = fixes::apply_all(tasks, &ctx.source, ctx.band.1);
    // "policy" means an org rule required the task: with no org rules the model's label is wrong (and
    // would shield the task from being deferred to fit a timebox).
    let plan_tasks: Vec<Task> = fixed
        .tasks
        .iter()
        .map(|t| {
            let relabel = ctx.policy.is_empty() && t.is_policy();
            Task {
                source: if relabel {
                    "derived".into()
                } else {
                    t.source.clone()
                },
                ..t.clone()
            }
        })
        .collect();
    let used: BTreeSet<i64> = plan_tasks.iter().map(|t| t.phase).collect();
    let phase_rows: Vec<Phase> = phases
        .into_iter()
        .filter(|(n, _)| used.contains(n))
        .map(|(number, name)| Phase { number, name })
        .collect();
    let (assumptions, dropped) = keep_assumptions(draft, &ctx.source);
    let mut notes: Vec<String> = ctx.notes.iter().chain(&fixed.notes).cloned().collect();
    notes.extend(
        plan_tasks
            .iter()
            .filter(|t| t.size == "XL")
            .map(|t| format!("{} is still XL: split it when you can.", t.id)),
    );
    notes.extend(
        plan_tasks
            .iter()
            .filter(|t| two_verbs(&t.title))
            .map(|t| format!("{} may hold two deliverables ({}).", t.id, t.title)),
    );
    if dropped > 0 {
        notes.push(format!(
            "Left out {dropped} assumption(s) not tied to your own words."
        ));
    }
    if plan_tasks.len() < ctx.band.0 {
        notes.push(format!(
            "{} tasks, fewer than the usual {} for this level of detail.",
            plan_tasks.len(),
            ctx.band.0
        ));
    }
    let policy_missing =
        policy_expects_task(&ctx.policy) && !plan_tasks.iter().any(Task::is_policy);
    if policy_missing {
        notes.push(
            "Your organization's rules ask for a task I could not place: add it by hand.".into(),
        );
    }
    let next_id = plan_tasks
        .iter()
        .map(|t| id_key(&t.id).1 + 1)
        .chain([ctx.next_id])
        .max()
        .unwrap_or(1);
    let ids: Vec<&str> = plan_tasks.iter().map(|t| t.id.as_str()).collect();
    let risks = keep_risks(draft, &mapping, &ids);
    Plan {
        goal: ctx.goal.clone(),
        constraints: constraints(draft, ctx),
        phases: phase_rows,
        risks,
        assumptions,
        notes: dedupe(notes),
        deadline: ctx.deadline.clone(),
        team_size: ctx.team_size,
        rate: ctx.rate,
        next_id,
        code_fixes: fixed.fixes,
        cycle_broken: fixed.cycle_broken,
        clamped: fixed.clamped,
        chunks: ctx.chunks,
        vague_used: ctx.vague_used,
        policy_missing,
        granularity: "normal".into(),
        source: ctx.source.clone(),
        tasks: plan_tasks,
        ..Plan::default()
    }
}

/// Phases with tasks, renumbered 1..k in their given order; a task in an unknown phase joins the last.
fn renumber_phases(draft: &Draft) -> (Vec<(i64, String)>, Vec<Task>) {
    let mut named: HashMap<i64, String> = HashMap::new();
    for (n, name) in &draft.phases {
        named.insert(*n, name.clone());
    }
    let numbers: BTreeSet<i64> = named
        .keys()
        .copied()
        .chain(draft.tasks.iter().map(|t| t.phase))
        .collect();
    let last = named
        .keys()
        .copied()
        .max()
        .or_else(|| numbers.iter().next_back().copied())
        .unwrap_or(1);
    let tasks: Vec<Task> = draft
        .tasks
        .iter()
        .map(|t| {
            if named.contains_key(&t.phase) || named.is_empty() {
                t.clone()
            } else {
                Task {
                    phase: last,
                    ..t.clone()
                }
            }
        })
        .collect();
    let used: BTreeSet<i64> = tasks.iter().map(|t| t.phase).collect();
    let renumber: HashMap<i64, i64> = used
        .iter()
        .enumerate()
        .map(|(i, old)| (*old, i as i64 + 1))
        .collect();
    let phases = used
        .iter()
        .map(|old| {
            let new = renumber.get(old).copied().unwrap_or(1);
            (
                new,
                named
                    .get(old)
                    .cloned()
                    .unwrap_or_else(|| format!("Phase {new}")),
            )
        })
        .collect();
    let tasks = tasks
        .into_iter()
        .map(|t| Task {
            phase: renumber.get(&t.phase).copied().unwrap_or(1),
            ..t
        })
        .collect();
    (phases, tasks)
}

fn assign_ids(tasks: Vec<Task>, ctx: &Context) -> (Vec<Task>, HashMap<String, String>) {
    if !ctx.renumber {
        let mapping = tasks.iter().map(|t| (t.id.clone(), t.id.clone())).collect();
        return (tasks, mapping);
    }
    let mut ordered: Vec<(usize, Task)> = tasks.into_iter().enumerate().collect();
    ordered.sort_by_key(|(i, t)| (t.phase, *i));
    let mut mapping: HashMap<String, String> = HashMap::new();
    for (i, (_, t)) in ordered.iter().enumerate() {
        mapping.insert(t.id.clone(), format!("T{}", i + 1));
    }
    let out = ordered
        .into_iter()
        .map(|(_, t)| Task {
            id: mapping.get(&t.id).cloned().unwrap_or_else(|| t.id.clone()),
            depends_on: t
                .depends_on
                .iter()
                .map(|d| mapping.get(d).cloned().unwrap_or_else(|| d.clone()))
                .collect(),
            ..t
        })
        .collect();
    (out, mapping)
}

fn keep_assumptions(draft: &Draft, source: &str) -> (Vec<Assumption>, usize) {
    let kept: Vec<_> = draft
        .assumptions
        .iter()
        .filter(|a| is_verbatim(&a.based_on, source))
        .collect();
    let rows = kept
        .iter()
        .take(MAX_ASSUMPTIONS)
        .enumerate()
        .map(|(i, a)| Assumption {
            id: format!("A{}", i + 1),
            text: a.text.clone(),
            based_on: a.based_on.clone(),
        })
        .collect();
    (rows, draft.assumptions.len() - kept.len())
}

fn keep_risks(draft: &Draft, mapping: &HashMap<String, String>, ids: &[&str]) -> Vec<Risk> {
    draft
        .risks
        .iter()
        .map(|r| {
            (
                mapping
                    .get(&r.task)
                    .cloned()
                    .unwrap_or_else(|| r.task.clone()),
                r.text.clone(),
            )
        })
        .filter(|(task, _)| ids.contains(&task.as_str()))
        .take(MAX_RISKS)
        .enumerate()
        .map(|(i, (task, text))| Risk {
            id: format!("R{}", i + 1),
            task,
            text,
        })
        .collect()
}

fn constraints(draft: &Draft, ctx: &Context) -> Vec<String> {
    let own = draft
        .constraints
        .iter()
        .filter(|c| mostly_grounded(c, &ctx.source))
        .cloned();
    let mut code: Vec<String> = ctx.known_constraints.clone();
    if let Some(n) = ctx.team_size.filter(|n| *n != 0) {
        code.push(format!("Team of {n}"));
    }
    if !ctx.deadline.is_empty() {
        code.push(format!("Deadline: {} (as you wrote it)", ctx.deadline));
    }
    let mut keys: Vec<String> = Vec::new();
    let mut out: Vec<String> = Vec::new();
    for text in code.into_iter().chain(own) {
        let key = norm(&text);
        if !keys.contains(&key) {
            keys.push(key);
            out.push(text);
        }
    }
    out
}
