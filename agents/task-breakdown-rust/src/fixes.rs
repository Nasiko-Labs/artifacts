//! Pure: what code repairs when the one repair call did not. Each fix makes new tasks (never edits the
//! input in place) and leaves a note the reply shows. Tasks here already carry their final ids.

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::Regex;

use crate::checks::{MAX_ACCEPTANCE, MAX_TITLE_CHARS, deps_of};
use crate::grounding::{is_verbatim, norm};
use crate::models::{OWNER_PLACEHOLDER, Task};
use crate::taskgraph::{SIZES, find_cycle, id_key, units_of};
use crate::util::{char_len, dedupe, re, take_chars};

pub const DEFAULT_SIZE: &str = "M";
pub const WHEN_PLACEHOLDER: &str = "[when?]";
const MONTH: &str = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?";

static TIME: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}(?:\s+\d{{4}})?|{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?|\d{{4}}-\d{{2}}-\d{{2}}|(?:mon|tues|wednes|thurs|fri|satur|sun)day|(?:\d+|a|one|two|three|four|five|six|half an?)[- ](?:hours?|days?|weeks?|months?|sprints?))\b"
    ))
});

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Fixed {
    pub tasks: Vec<Task>,
    pub notes: Vec<String>,
    pub fixes: usize,
    pub cycle_broken: bool,
    pub clamped: bool,
}

impl Fixed {
    fn add(&self, tasks: Vec<Task>, note: String) -> Fixed {
        let mut notes = self.notes.clone();
        notes.push(note);
        Fixed {
            tasks,
            notes,
            fixes: self.fixes + 1,
            ..self.clone()
        }
    }
}

type Step = fn(Fixed, &str) -> Fixed;

pub fn apply_all(tasks: Vec<Task>, source: &str, max_tasks: usize) -> Fixed {
    let steps: [Step; 7] = [
        dedupe_titles,
        sizes,
        acceptance,
        titles,
        owners,
        times,
        dangling,
    ];
    let fixed = steps.iter().fold(
        Fixed {
            tasks,
            ..Fixed::default()
        },
        |acc, step| step(acc, source),
    );
    break_cycles(clamp(break_cycles(fixed), max_tasks))
}

fn units(task: &Task) -> i64 {
    units_of(&task.size).unwrap_or(2)
}

/// Dependencies on `gone` point at `keep` instead (or are dropped when `keep` is None).
fn redirect(tasks: &[Task], gone: &str, keep: Option<&str>) -> Vec<Task> {
    tasks
        .iter()
        .map(|t| {
            let deps = t.depends_on.iter().filter_map(|d| {
                let to = if d == gone { keep } else { Some(d.as_str()) };
                to.filter(|to| *to != t.id).map(str::to_string)
            });
            Task {
                depends_on: dedupe(deps),
                ..t.clone()
            }
        })
        .collect()
}

fn dedupe_titles(fixed: Fixed, _source: &str) -> Fixed {
    let mut fixed = fixed;
    while let Some((gone, first)) = first_repeat(&fixed.tasks) {
        let kept: Vec<Task> = fixed
            .tasks
            .iter()
            .filter(|x| x.id != gone)
            .cloned()
            .collect();
        let note = format!("{gone} repeated {first} and was merged into it.");
        fixed = fixed.add(redirect(&kept, &gone, Some(&first)), note);
    }
    fixed
}

/// The first task whose title repeats an earlier one: (its id, the earlier task's id).
fn first_repeat(tasks: &[Task]) -> Option<(String, String)> {
    let mut seen: HashMap<String, String> = HashMap::new();
    for t in tasks {
        let key = norm(&t.title);
        if let Some(first) = seen.get(&key) {
            return Some((t.id.clone(), first.clone()));
        }
        seen.insert(key, t.id.clone());
    }
    None
}

fn sizes(fixed: Fixed, _source: &str) -> Fixed {
    let bad: Vec<String> = fixed
        .tasks
        .iter()
        .filter(|t| units_of(&t.size).is_none())
        .map(|t| t.id.clone())
        .collect();
    if bad.is_empty() {
        return fixed;
    }
    let tasks = fixed
        .tasks
        .iter()
        .map(|t| {
            if bad.contains(&t.id) {
                Task {
                    size: DEFAULT_SIZE.into(),
                    ..t.clone()
                }
            } else {
                t.clone()
            }
        })
        .collect();
    fixed.add(
        tasks,
        format!("{}: no valid size, set to {DEFAULT_SIZE}.", bad.join(", ")),
    )
}

fn acceptance(fixed: Fixed, _source: &str) -> Fixed {
    let empty: Vec<String> = fixed
        .tasks
        .iter()
        .filter(|t| t.acceptance.is_empty())
        .map(|t| t.id.clone())
        .collect();
    let long: Vec<String> = fixed
        .tasks
        .iter()
        .filter(|t| t.acceptance.len() > MAX_ACCEPTANCE)
        .map(|t| t.id.clone())
        .collect();
    let mut tasks: Vec<Task> = fixed
        .tasks
        .iter()
        .filter(|t| !empty.contains(&t.id))
        .map(|t| Task {
            acceptance: t.acceptance.iter().take(MAX_ACCEPTANCE).cloned().collect(),
            ..t.clone()
        })
        .collect();
    for gone in &empty {
        tasks = redirect(&tasks, gone, None);
    }
    let mut out = fixed;
    if !long.is_empty() {
        let note = format!(
            "{}: kept the first {MAX_ACCEPTANCE} acceptance criteria.",
            long.join(", ")
        );
        out = out.add(tasks.clone(), note);
    }
    if !empty.is_empty() {
        out = out.add(
            tasks,
            format!("{}: removed, no acceptance criteria.", empty.join(", ")),
        );
    }
    out
}

fn titles(fixed: Fixed, _source: &str) -> Fixed {
    let long: Vec<String> = fixed
        .tasks
        .iter()
        .filter(|t| char_len(&t.title) > MAX_TITLE_CHARS)
        .map(|t| t.id.clone())
        .collect();
    if long.is_empty() {
        return fixed;
    }
    let tasks = fixed
        .tasks
        .iter()
        .map(|t| {
            if long.contains(&t.id) {
                let short = format!("{}…", take_chars(&t.title, MAX_TITLE_CHARS - 1).trim_end());
                Task {
                    title: short,
                    ..t.clone()
                }
            } else {
                t.clone()
            }
        })
        .collect();
    fixed.add(
        tasks,
        format!(
            "{}: title shortened to {MAX_TITLE_CHARS} characters.",
            long.join(", ")
        ),
    )
}

fn owners(fixed: Fixed, source: &str) -> Fixed {
    let bad: Vec<String> = fixed
        .tasks
        .iter()
        .filter(|t| t.owner != OWNER_PLACEHOLDER && !is_verbatim(&t.owner, source))
        .map(|t| t.id.clone())
        .collect();
    if bad.is_empty() {
        return fixed;
    }
    let tasks = fixed
        .tasks
        .iter()
        .map(|t| {
            if bad.contains(&t.id) {
                Task {
                    owner: OWNER_PLACEHOLDER.into(),
                    ..t.clone()
                }
            } else {
                t.clone()
            }
        })
        .collect();
    let note = format!(
        "{}: owner you didn't name replaced with {OWNER_PLACEHOLDER}.",
        bad.join(", ")
    );
    fixed.add(tasks, note)
}

/// Dates and calendar durations the user did not write are replaced with a placeholder.
pub fn strip_times(text: &str, source: &str) -> String {
    TIME.replace_all(text, |caps: &regex::Captures| {
        let found = caps.get(0).map_or("", |m| m.as_str());
        if is_verbatim(found, source) {
            found.to_string()
        } else {
            WHEN_PLACEHOLDER.to_string()
        }
    })
    .into_owned()
}

fn times(fixed: Fixed, source: &str) -> Fixed {
    let tasks: Vec<Task> = fixed
        .tasks
        .iter()
        .map(|t| Task {
            title: strip_times(&t.title, source),
            acceptance: t
                .acceptance
                .iter()
                .map(|a| strip_times(a, source))
                .collect(),
            ..t.clone()
        })
        .collect();
    let changed: Vec<String> = tasks
        .iter()
        .zip(&fixed.tasks)
        .filter(|(new, old)| new != old)
        .map(|(new, _)| new.id.clone())
        .collect();
    if changed.is_empty() {
        return fixed;
    }
    fixed.add(
        tasks,
        format!(
            "{}: removed dates or durations you didn't give.",
            changed.join(", ")
        ),
    )
}

fn dangling(fixed: Fixed, _source: &str) -> Fixed {
    let ids: Vec<&str> = fixed.tasks.iter().map(|t| t.id.as_str()).collect();
    let dropped: Vec<(String, String)> = fixed
        .tasks
        .iter()
        .flat_map(|t| {
            t.depends_on
                .iter()
                .filter(|d| !ids.contains(&d.as_str()) || **d == t.id)
                .map(|d| (t.id.clone(), d.clone()))
        })
        .collect();
    if dropped.is_empty() {
        return fixed;
    }
    let tasks = fixed
        .tasks
        .iter()
        .map(|t| Task {
            depends_on: t
                .depends_on
                .iter()
                .filter(|d| ids.contains(&d.as_str()) && **d != t.id)
                .cloned()
                .collect(),
            ..t.clone()
        })
        .collect();
    let edges: Vec<String> = dropped
        .iter()
        .map(|(a, b)| format!("{a} needs {b}"))
        .collect();
    fixed.add(
        tasks,
        format!(
            "Dropped dependencies on tasks that don't exist ({}).",
            edges.join(", ")
        ),
    )
}

fn break_cycles(fixed: Fixed) -> Fixed {
    let Some(cycle) = find_cycle(&deps_of(&fixed.tasks)) else {
        return fixed;
    };
    let unit_of: HashMap<&str, i64> = fixed
        .tasks
        .iter()
        .map(|t| (t.id.as_str(), units(t)))
        .collect();
    let Some((needer, needed)) = cycle
        .windows(2)
        .map(|w| (w[0].clone(), w[1].clone()))
        .min_by_key(|(_, needed)| {
            (
                unit_of.get(needed.as_str()).copied().unwrap_or(2),
                id_key(needed),
            )
        })
    else {
        return fixed;
    };
    let tasks = fixed
        .tasks
        .iter()
        .map(|t| {
            if t.id == needer {
                Task {
                    depends_on: t
                        .depends_on
                        .iter()
                        .filter(|d| **d != needed)
                        .cloned()
                        .collect(),
                    ..t.clone()
                }
            } else {
                t.clone()
            }
        })
        .collect();
    let note = format!(
        "Dependency loop {}: I removed '{needer} needs {needed}'. Check that order.",
        cycle.join(" -> ")
    );
    let next = Fixed {
        cycle_broken: true,
        ..fixed.add(tasks, note)
    };
    break_cycles(next)
}

fn clamp(fixed: Fixed, max_tasks: usize) -> Fixed {
    if fixed.tasks.len() <= max_tasks {
        return fixed;
    }
    let mut by_phase: Vec<(i64, Vec<&Task>)> = Vec::new();
    for t in &fixed.tasks {
        match by_phase.iter_mut().find(|(p, _)| *p == t.phase) {
            Some((_, group)) => group.push(t),
            None => by_phase.push((t.phase, vec![t])),
        }
    }
    let Some((_, group)) = by_phase
        .iter()
        .min_by_key(|(p, group)| (-(group.len() as i64), *p))
    else {
        return fixed;
    };
    if group.len() < 2 {
        return fixed;
    }
    let (keep, gone) = (
        group[group.len() - 2].clone(),
        group[group.len() - 1].clone(),
    );
    let total = units(&keep) + units(&gone);
    let size = SIZES
        .iter()
        .find(|(_, u)| *u >= total)
        .map_or("XL", |(s, _)| s);
    let merged = Task {
        size: size.into(),
        acceptance: keep
            .acceptance
            .iter()
            .chain(&gone.acceptance)
            .take(MAX_ACCEPTANCE)
            .cloned()
            .collect(),
        depends_on: dedupe(
            keep.depends_on
                .iter()
                .chain(&gone.depends_on)
                .filter(|d| **d != keep.id && **d != gone.id)
                .cloned(),
        ),
        ..keep.clone()
    };
    let tasks: Vec<Task> = fixed
        .tasks
        .iter()
        .filter(|t| t.id != gone.id)
        .map(|t| {
            if t.id == keep.id {
                merged.clone()
            } else {
                t.clone()
            }
        })
        .collect();
    let note = format!(
        "{} ({}) merged into {} to stay within {max_tasks} tasks.",
        gone.id, gone.title, keep.id
    );
    let next = Fixed {
        clamped: true,
        ..fixed.add(redirect(&tasks, &gone.id, Some(&keep.id)), note)
    };
    clamp(next, max_tasks)
}
