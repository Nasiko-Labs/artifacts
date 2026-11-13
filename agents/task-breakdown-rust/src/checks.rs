//! Pure: read the model's plan JSON into a normalised draft, and list exactly what is wrong with it (the
//! text of the one repair call). Nothing here fixes anything; `fixes.rs` does that when the repair fails.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::models::{OWNER_PLACEHOLDER, SOURCES, Task};
use crate::taskgraph::{find_cycle, units_of};
use crate::util::{char_len, py_repr, re};

pub const MAX_TITLE_CHARS: usize = 80;
pub const MAX_ACCEPTANCE: usize = 3;
pub const PHASE_RANGE: (usize, usize) = (3, 6);
pub const MAX_RISKS: usize = 5;
pub const MAX_ASSUMPTIONS: usize = 6;
const VERBS: &[&str] = &[
    "add",
    "analyse",
    "analyze",
    "announce",
    "approve",
    "audit",
    "build",
    "buy",
    "choose",
    "clean",
    "collect",
    "configure",
    "create",
    "decide",
    "define",
    "deploy",
    "design",
    "document",
    "draft",
    "estimate",
    "evaluate",
    "fix",
    "gather",
    "hire",
    "implement",
    "integrate",
    "interview",
    "launch",
    "measure",
    "migrate",
    "monitor",
    "onboard",
    "plan",
    "prepare",
    "present",
    "publish",
    "prototype",
    "recruit",
    "refactor",
    "release",
    "remove",
    "research",
    "review",
    "run",
    "schedule",
    "secure",
    "select",
    "send",
    "set",
    "ship",
    "sign",
    "specify",
    "test",
    "train",
    "translate",
    "update",
    "upgrade",
    "validate",
    "verify",
    "write",
];

static VERIFY: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)verif|test|validat|integrat|review|check|\bqa\b|accept|sign[- ]off|rehears|dry[- ]run")
});
static POLICY_TASK: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)(?:every|each|all|any) (?:work )?(?:plan|project)s? (?:needs?|must (?:have|include|contain)|should (?:have|include)|requires?|has to (?:have|include)) (?:a |an )?([^.\n]{3,60})",
    )
});
static WORD: LazyLock<Regex> = LazyLock::new(|| re(r"[a-z]+"));

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DraftRisk {
    pub task: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DraftAssumption {
    pub text: String,
    pub based_on: String,
}

/// The model's plan, normalised; ids are still the model's own keys.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Draft {
    pub phases: Vec<(i64, String)>,
    pub tasks: Vec<Task>,
    pub risks: Vec<DraftRisk>,
    pub assumptions: Vec<DraftAssumption>,
    pub constraints: Vec<String>,
}

impl Draft {
    pub fn to_json(&self) -> Value {
        json!({
            "phases": self.phases.iter().map(|(n, name)| json!({"id": n, "name": name})).collect::<Vec<_>>(),
            "tasks": self.tasks,
            "risks": self.risks.iter().map(|r| json!({"task": r.task, "text": r.text})).collect::<Vec<_>>(),
            "assumptions": self.assumptions.iter()
                .map(|a| json!({"text": a.text, "based_on": a.based_on})).collect::<Vec<_>>(),
            "constraints": self.constraints,
        })
    }
}

fn s(value: Option<&Value>) -> String {
    value
        .and_then(Value::as_str)
        .map_or(String::new(), |v| v.trim().to_string())
}

fn strs(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|v| s(Some(v)))
                .filter(|v| !v.is_empty())
                .collect()
        })
        .unwrap_or_default()
}

/// Python's `int(value)` with a default for anything it would refuse.
pub fn int_of(value: Option<&Value>, default: i64) -> i64 {
    match value {
        Some(Value::Number(n)) => n
            .as_i64()
            .or_else(|| {
                n.as_f64()
                    .filter(|f| f.is_finite())
                    .map(|f| f.trunc() as i64)
            })
            .unwrap_or(default),
        Some(Value::String(text)) => text.trim().parse().unwrap_or(default),
        Some(Value::Bool(b)) => i64::from(*b),
        _ => default,
    }
}

fn list<'a>(raw: &'a Map<String, Value>, key: &str) -> &'a [Value] {
    raw.get(key)
        .and_then(Value::as_array)
        .map_or(&[], Vec::as_slice)
}

pub fn normalize(raw: &Map<String, Value>) -> Draft {
    let phases: Vec<(i64, String)> = list(raw, "phases")
        .iter()
        .enumerate()
        .filter_map(|(i, p)| {
            let p = p.as_object()?;
            let number = int_of(p.get("id").or_else(|| p.get("number")), i as i64 + 1);
            let name = s(p.get("name"));
            Some((
                number,
                if name.is_empty() {
                    format!("Phase {}", i + 1)
                } else {
                    name
                },
            ))
        })
        .collect();
    let default_phase = phases.last().map_or(1, |p| p.0);
    let mut tasks: Vec<Task> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for (i, item) in list(raw, "tasks").iter().enumerate() {
        let Some(item) = item.as_object() else {
            continue;
        };
        let title = s(item.get("title"));
        if title.is_empty() {
            continue;
        }
        let mut key = s(item.get("id"));
        if key.is_empty() || seen.contains(&key) {
            key = format!("X{}", i + 1);
        }
        seen.insert(key.clone());
        let source = s(item.get("source")).to_lowercase();
        let owner = s(item.get("owner"));
        tasks.push(Task {
            id: key,
            phase: int_of(item.get("phase"), default_phase),
            title,
            size: s(item.get("size")).to_uppercase(),
            depends_on: strs(item.get("depends_on")),
            acceptance: strs(item.get("acceptance")),
            owner: if owner.is_empty() {
                OWNER_PLACEHOLDER.into()
            } else {
                owner
            },
            source: if SOURCES.contains(&source.as_str()) {
                source
            } else {
                "derived".into()
            },
            status: if s(item.get("status")).to_lowercase() == "done" {
                "done"
            } else {
                "pending"
            }
            .into(),
        });
    }
    let risks = list(raw, "risks")
        .iter()
        .filter_map(Value::as_object)
        .filter(|r| !s(r.get("text")).is_empty())
        .map(|r| DraftRisk {
            task: s(r.get("task")),
            text: s(r.get("text")),
        })
        .collect();
    let assumptions = list(raw, "assumptions")
        .iter()
        .filter_map(Value::as_object)
        .filter(|a| !s(a.get("text")).is_empty())
        .map(|a| DraftAssumption {
            text: s(a.get("text")),
            based_on: s(a.get("based_on")),
        })
        .collect();
    Draft {
        phases,
        tasks,
        risks,
        assumptions,
        constraints: strs(raw.get("constraints")),
    }
}

pub fn two_verbs(title: &str) -> bool {
    let lowered = title.to_lowercase();
    let words: Vec<&str> = WORD.find_iter(&lowered).map(|m| m.as_str()).collect();
    words
        .iter()
        .enumerate()
        .any(|(i, w)| *w == "and" && i > 0 && i + 1 < words.len() && VERBS.contains(&words[i + 1]))
}

pub fn policy_expects_task(policy: &[String]) -> bool {
    policy.iter().any(|p| POLICY_TASK.is_match(p))
}

pub fn deps_of(tasks: &[Task]) -> Vec<(String, Vec<String>)> {
    tasks
        .iter()
        .map(|t| (t.id.clone(), t.depends_on.clone()))
        .collect()
}

/// Every rule the draft breaks, worded for the repair call.
pub fn problems(draft: &Draft, band: (usize, usize), policy: &[String]) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    let ids: HashSet<&str> = draft.tasks.iter().map(|t| t.id.as_str()).collect();
    for t in &draft.tasks {
        found.extend(task_problems(t, &ids));
    }
    if let Some(cycle) = find_cycle(&deps_of(&draft.tasks)) {
        found.push(format!(
            "dependency cycle {}: remove one dependency so no task waits on itself",
            cycle.join(" -> ")
        ));
    }
    let (low, high) = band;
    let count = draft.tasks.len();
    if !(low..=high).contains(&count) {
        found.push(format!(
            "{count} tasks: the plan needs between {low} and {high} tasks"
        ));
    }
    let used: HashSet<i64> = draft.tasks.iter().map(|t| t.phase).collect();
    let (pmin, pmax) = PHASE_RANGE;
    if count >= pmin && !(pmin..=pmax).contains(&used.len()) {
        found.push(format!(
            "{} phases: use {pmin}-{pmax} phases named by outcome",
            used.len()
        ));
    }
    let last = used.iter().copied().max().unwrap_or(0);
    if !draft.tasks.is_empty()
        && !draft
            .tasks
            .iter()
            .any(|t| t.phase == last && VERIFY.is_match(&t.title))
    {
        found.push("the last phase has no verification or integration task: add one".into());
    }
    if policy_expects_task(policy) && !draft.tasks.iter().any(Task::is_policy) {
        found.push(
            "the ORG RULES require a task that is missing: add it with source \"policy\"".into(),
        );
    }
    found
}

fn task_problems(task: &Task, ids: &HashSet<&str>) -> Vec<String> {
    let tid = &task.id;
    let mut out = Vec::new();
    if units_of(&task.size).is_none() {
        let shown = if task.size.is_empty() {
            "(none)"
        } else {
            task.size.as_str()
        };
        out.push(format!(
            "{tid}: size {} is not one of S, M, L, XL",
            py_repr(shown)
        ));
    } else if task.size == "XL" {
        out.push(format!(
            "{tid}: size XL is too big: split it into smaller tasks"
        ));
    }
    if task.acceptance.is_empty() {
        out.push(format!(
            "{tid}: no acceptance criteria: add 1-3 observable checks"
        ));
    } else if task.acceptance.len() > MAX_ACCEPTANCE {
        out.push(format!(
            "{tid}: {} acceptance criteria: keep at most {MAX_ACCEPTANCE}",
            task.acceptance.len()
        ));
    }
    if char_len(&task.title) > MAX_TITLE_CHARS {
        out.push(format!(
            "{tid}: title is longer than {MAX_TITLE_CHARS} characters"
        ));
    }
    if two_verbs(&task.title) {
        out.push(format!(
            "{tid}: title {} joins two verbs: one deliverable per task, split it",
            py_repr(&task.title)
        ));
    }
    for dep in &task.depends_on {
        if !ids.contains(dep.as_str()) || dep == tid {
            out.push(format!(
                "{tid}: depends_on {} is not another task id in the plan",
                py_repr(dep)
            ));
        }
    }
    out
}
