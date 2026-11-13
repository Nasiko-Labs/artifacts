//! Pure: reconcile the model's revised plan with the recovered one. Code owns ids: a task the instruction
//! did not name keeps exactly its earlier fields (a model that rewrote it is reverted); a task the model
//! dropped comes back unless the instruction removes or merges something; new tasks get fresh ids T{n+1}...

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use regex::Regex;

use crate::checks::Draft;
use crate::models::{Plan, Task};
use crate::util::{cap, dedupe, re};

static NAMED: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\bT(\d+)\b"));
static REMOVES: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)\b(?:drop|remove|delete|merge|combine|cut|skip|without|get rid of|fold)\b")
});
static SPLITS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\b(?:split|break up|break down|divide|replace)\b"));

pub fn named_ids(instruction: &str) -> HashSet<String> {
    NAMED
        .captures_iter(instruction)
        .map(|c| format!("T{}", cap(&c, 1)))
        .collect()
}

/// (draft with final ids, next id number, notes).
pub fn reconcile(old: &Plan, draft: &Draft, instruction: &str) -> (Draft, i64, Vec<String>) {
    let named = named_ids(instruction);
    let split: HashSet<String> = if SPLITS.is_match(instruction) {
        named.clone()
    } else {
        HashSet::new()
    };
    let old_by_id: HashMap<&str, &Task> = old.tasks.iter().map(|t| (t.id.as_str(), t)).collect();
    let mut next_id = old.next_id;
    let mut rename: HashMap<String, String> = HashMap::new();
    let mut tasks: Vec<Task> = Vec::new();
    for t in &draft.tasks {
        let new_id = if old_by_id.contains_key(t.id.as_str())
            && !split.contains(&t.id)
            && !rename.contains_key(&t.id)
        {
            t.id.clone()
        } else {
            next_id += 1;
            format!("T{}", next_id - 1)
        };
        rename.insert(t.id.clone(), new_id.clone());
        let before = old_by_id.get(new_id.as_str());
        let status = match before {
            Some(b) if t.status == "pending" => b.status.clone(),
            _ => t.status.clone(),
        };
        tasks.push(Task {
            id: new_id,
            status,
            ..t.clone()
        });
    }
    let tasks: Vec<Task> = tasks
        .into_iter()
        .map(|t| Task {
            depends_on: t
                .depends_on
                .iter()
                .map(|d| rename.get(d).cloned().unwrap_or_else(|| d.clone()))
                .collect(),
            ..t
        })
        .collect();
    let remaining: HashSet<&str> = tasks
        .iter()
        .map(|t| t.id.as_str())
        .filter(|id| old_by_id.contains_key(id))
        .collect();
    let mut reverted: Vec<String> = Vec::new();
    let mut out: Vec<Task> = Vec::new();
    for t in &tasks {
        match old_by_id.get(t.id.as_str()) {
            Some(before)
                if !named.contains(&t.id) && changed(before, t, &remaining, &old_by_id) =>
            {
                reverted.push(t.id.clone());
                out.push(Task {
                    depends_on: kept_deps(before, t, &old_by_id),
                    ..(*before).clone()
                });
            }
            _ => out.push(t.clone()),
        }
    }
    let present: HashSet<String> = out.iter().map(|t| t.id.clone()).collect();
    if !REMOVES.is_match(instruction) {
        let missing = old.tasks.iter().filter(|t| {
            !present.contains(&t.id) && !named.contains(&t.id) && !split.contains(&t.id)
        });
        out.extend(missing.cloned().collect::<Vec<_>>());
    }
    let notes = if reverted.is_empty() {
        Vec::new()
    } else {
        vec![format!(
            "Kept {} as they were: the change didn't name them.",
            reverted.join(", ")
        )]
    };
    (
        Draft {
            tasks: out,
            ..draft.clone()
        },
        next_id,
        notes,
    )
}

/// A field differs, or the dependencies among earlier tasks that still exist differ (wiring a new task
/// in, or losing a removed one, is not a change).
fn changed(
    before: &Task,
    after: &Task,
    remaining: &HashSet<&str>,
    old_by_id: &HashMap<&str, &Task>,
) -> bool {
    if before.title != after.title
        || before.size != after.size
        || before.acceptance != after.acceptance
        || before.owner != after.owner
        || before.source != after.source
        || before.status != after.status
    {
        return true;
    }
    let was: HashSet<&str> = before
        .depends_on
        .iter()
        .map(String::as_str)
        .filter(|d| remaining.contains(d))
        .collect();
    let now: HashSet<&str> = after
        .depends_on
        .iter()
        .map(String::as_str)
        .filter(|d| old_by_id.contains_key(d))
        .collect();
    was != now
}

/// The earlier dependencies, plus any new task the model wired in (new ids are not in the earlier plan).
fn kept_deps(before: &Task, after: &Task, old_by_id: &HashMap<&str, &Task>) -> Vec<String> {
    let new = after
        .depends_on
        .iter()
        .filter(|d| !old_by_id.contains_key(d.as_str()) && !before.depends_on.contains(d))
        .cloned();
    dedupe(before.depends_on.iter().cloned().chain(new))
}
