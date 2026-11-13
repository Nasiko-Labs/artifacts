//! Pure: read a plan back from text this agent rendered earlier (render.rs tables, planfile.rs
//! task_plan.md, or the fenced json envelope), so a follow-up can work on it without any stored state.
//!
//! Each reader returns plain parts (see `build`) or None. A reader refuses a partial plan: the table form
//! must hold exactly the task and phase counts its header states, task_plan.md must reach its closing
//! sections. A trimmed or garbled reply is therefore "not readable", never a smaller plan.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use crate::checks::deps_of;
use crate::models::{Assumption, OWNER_PLACEHOLDER, POLICY_MARK, Phase, Plan, Risk, SOURCES, Task};
use crate::taskgraph::{find_cycle, id_key, units_of};
use crate::util::{cap, lines, num, re};

static HEAD: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?m)^\*\*Plan: (.*?)\*\*\s+·\s+(\d+) tasks · (\d+) phases"));
static PHASE: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\*\*Phase (\d+): (.*?)\*\* \(\d+ tasks, \d+ units\)\s*$"));
static ROW: LazyLock<Regex> = LazyLock::new(|| re(r"^\|\s*(T\d+)\s*\|(.*)\|\s*$"));
static DONE: LazyLock<Regex> = LazyLock::new(|| re(r"^~~(.*)~~ \(done\)$"));
static LABEL: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\*\*(Constraints|Assumptions|Risks):\*\* (.*)$"));
static ASSUMPTION: LazyLock<Regex> = LazyLock::new(|| re(r#"^(A\d+) (.*) \("(.*)"\)$"#));
static RISK: LazyLock<Regex> = LazyLock::new(|| re(r"^(R\d+) \((T\d+)\) (.*)$"));
static LANES: LazyLock<Regex> = LazyLock::new(|| re(r"\*\*Lanes for (\d+) (?:person|people)\*\*"));
static FILE_HEAD: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^# Task plan: (.*)$"));
static FILE_PHASE: LazyLock<Regex> =
    LazyLock::new(|| re(r"^### Phase (\d+): (.*?)\s+\[[ x]\] (?:done|pending)\s*$"));
static FILE_TASK: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^- \[( |x)\] (T\d+) (.*) \((S|M|L|XL)\) — owner: (.*?) — needs: (.*?) — done when: (.*)$")
});
static FILE_ASSUMPTION: LazyLock<Regex> =
    LazyLock::new(|| re(r#"^- (A\d+) (.*) \(from: "(.*)"\)$"#));
static FILE_RISK: LazyLock<Regex> = LazyLock::new(|| re(r"^- (R\d+) risk on (T\d+): (.*)$"));
static TEAM: LazyLock<Regex> = LazyLock::new(|| re(r"^Team of (\d+)$"));
static DEADLINE: LazyLock<Regex> = LazyLock::new(|| re(r"^Deadline: (.*) \(as you wrote it\)$"));
static JSON: LazyLock<Regex> = LazyLock::new(|| re(r"(?s)```(?:json)?\s*(\{.*\})\s*```"));

/// What a reader found: the plan's pieces as text, before validation.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Parts {
    pub goal: String,
    pub phases: Vec<(i64, String)>,
    pub tasks: Vec<Task>,
    pub constraints: Vec<String>,
    /// (id, text, based_on)
    pub assumptions: Vec<(String, String, String)>,
    /// (id, task, text)
    pub risks: Vec<(String, String, String)>,
    pub team: Option<i64>,
}

fn ids_of(text: &str) -> Vec<String> {
    text.split(',')
        .map(str::trim)
        .filter(|i| !i.is_empty() && *i != "none")
        .map(str::to_string)
        .collect()
}

struct RawTask<'a> {
    id: &'a str,
    phase: i64,
    title: &'a str,
    size: &'a str,
    needs: &'a str,
    owner: &'a str,
    done_when: &'a str,
    done: bool,
}

fn task(raw: RawTask) -> Task {
    let title = raw.title.trim();
    let policy = title.ends_with(POLICY_MARK);
    let title = title.strip_suffix(POLICY_MARK).unwrap_or(title).trim();
    Task {
        id: raw.id.to_string(),
        phase: raw.phase,
        title: title.to_string(),
        size: raw.size.trim().to_string(),
        depends_on: ids_of(raw.needs),
        acceptance: raw
            .done_when
            .split("; ")
            .map(str::trim)
            .filter(|a| !a.is_empty())
            .map(str::to_string)
            .collect(),
        owner: raw.owner.trim().to_string(),
        source: if policy { "policy" } else { "derived" }.into(),
        status: if raw.done { "done" } else { "pending" }.into(),
    }
}

/// The table cells between the id and the closing pipe, split on pipes that are not escaped.
fn cells(inner: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    let mut previous = '\0';
    for ch in inner.chars() {
        if ch == '|' && previous != '\\' {
            out.push(std::mem::take(&mut current));
        } else {
            current.push(ch);
        }
        previous = ch;
    }
    out.push(current);
    out.into_iter()
        .map(|c| c.trim().replace("\\|", "|"))
        .collect()
}

fn row(line: &str, phase: i64) -> Option<Task> {
    let caps = ROW.captures(line)?;
    let cells = cells(cap(&caps, 2));
    if cells.len() != 5 {
        return None;
    }
    let done = DONE.captures(&cells[0]);
    let title = done.as_ref().map_or(cells[0].as_str(), |d| cap(d, 1));
    Some(task(RawTask {
        id: cap(&caps, 1),
        phase,
        title,
        size: &cells[1],
        needs: &cells[2],
        owner: &cells[3],
        done_when: &cells[4],
        done: done.is_some(),
    }))
}

pub fn from_table(text: &str) -> Option<Parts> {
    let head = HEAD.captures(text)?;
    let end = head.get(0).map_or(0, |m| m.end());
    let mut parts = Parts {
        goal: cap(&head, 1).to_string(),
        ..Parts::default()
    };
    let mut phase = 0;
    for line in lines(&text[end..]) {
        if let Some(caps) = PHASE.captures(line) {
            phase = num(cap(&caps, 1));
            parts.phases.push((phase, cap(&caps, 2).to_string()));
        } else if line.starts_with('|') {
            parts.tasks.extend(row(line, phase));
        } else if let Some(caps) = LABEL.captures(line) {
            labelled(
                &mut parts,
                cap(&caps, 1),
                cap(&caps, 2).split(" · ").collect(),
            );
        }
    }
    parts.team = LANES.captures(text).map(|c| num(cap(&c, 1)));
    let complete = parts.tasks.len() as i64 == num(cap(&head, 2))
        && parts.phases.len() as i64 == num(cap(&head, 3));
    complete.then_some(parts)
}

fn labelled(parts: &mut Parts, label: &str, items: Vec<&str>) {
    match label {
        "Constraints" => {
            parts.constraints = items
                .iter()
                .map(|i| i.trim())
                .filter(|i| !i.is_empty())
                .map(str::to_string)
                .collect();
        }
        "Assumptions" => {
            for item in items {
                if let Some(c) = ASSUMPTION.captures(item.trim()) {
                    parts.assumptions.push((
                        cap(&c, 1).into(),
                        cap(&c, 2).into(),
                        cap(&c, 3).into(),
                    ));
                }
            }
        }
        _ => {
            for item in items {
                if let Some(c) = RISK.captures(item.trim()) {
                    parts
                        .risks
                        .push((cap(&c, 1).into(), cap(&c, 2).into(), cap(&c, 3).into()));
                }
            }
        }
    }
}

pub fn from_file(text: &str) -> Option<Parts> {
    let head = FILE_HEAD.captures(text)?;
    let end = head.get(0).map_or(0, |m| m.end());
    if !text[end..].contains("## Critical path") {
        return None;
    }
    let mut parts = Parts {
        goal: cap(&head, 1).to_string(),
        ..Parts::default()
    };
    let (mut section, mut phase) = (String::new(), 0);
    for line in lines(&text[end..]) {
        if let Some(rest) = line.strip_prefix("## ") {
            section = rest.trim().to_string();
        } else if let Some(c) = FILE_PHASE.captures(line) {
            phase = num(cap(&c, 1));
            parts.phases.push((phase, cap(&c, 2).to_string()));
        } else if let Some(c) = FILE_TASK.captures(line) {
            parts.tasks.push(task(RawTask {
                id: cap(&c, 2),
                phase,
                title: cap(&c, 3),
                size: cap(&c, 4),
                needs: cap(&c, 6),
                owner: cap(&c, 5),
                done_when: cap(&c, 7),
                done: cap(&c, 1) == "x",
            }));
        } else if section == "Constraints" && line.starts_with("- ") && line != "- none stated" {
            parts.constraints.push(line[2..].trim().to_string());
        } else if let Some(c) = FILE_ASSUMPTION.captures(line) {
            parts
                .assumptions
                .push((cap(&c, 1).into(), cap(&c, 2).into(), cap(&c, 3).into()));
        } else if let Some(c) = FILE_RISK.captures(line) {
            parts
                .risks
                .push((cap(&c, 1).into(), cap(&c, 2).into(), cap(&c, 3).into()));
        }
    }
    (!parts.tasks.is_empty() && !parts.phases.is_empty()).then_some(parts)
}

fn text_of(value: &Value) -> Option<String> {
    value.as_str().map(str::to_string)
}

fn int_strict(value: &Value) -> Option<i64> {
    match value {
        Value::Number(n) => n.as_i64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

fn json_task(item: &Value) -> Option<Task> {
    let joined = |key: &str| -> Option<Vec<String>> {
        match item.get(key) {
            None | Some(Value::Null) => Some(Vec::new()),
            Some(Value::Array(values)) => values.iter().map(text_of).collect(),
            Some(_) => None,
        }
    };
    let owner = item
        .get("owner")
        .and_then(Value::as_str)
        .filter(|o| !o.is_empty())
        .unwrap_or(OWNER_PLACEHOLDER);
    let needs = joined("depends_on")?.join(", ");
    let done_when = joined("acceptance")?.join("; ");
    let id = text_of(item.get("id")?)?;
    let title = text_of(item.get("title")?)?;
    let size = text_of(item.get("size")?)?;
    let parsed = task(RawTask {
        id: &id,
        phase: int_strict(item.get("phase")?)?,
        title: &title,
        size: &size,
        needs: &needs,
        owner,
        done_when: &done_when,
        done: item.get("status").and_then(Value::as_str) == Some("done"),
    });
    let source = item
        .get("source")
        .and_then(Value::as_str)
        .filter(|s| SOURCES.contains(s))
        .unwrap_or("derived");
    Some(Task {
        source: source.into(),
        ..parsed
    })
}

fn triples(env: &Value, key: &str, fields: [&str; 3]) -> Option<Vec<(String, String, String)>> {
    let Some(items) = env.get(key).filter(|v| !v.is_null()) else {
        return Some(Vec::new());
    };
    items
        .as_array()?
        .iter()
        .map(|a| {
            Some((
                text_of(a.get(fields[0])?)?,
                text_of(a.get(fields[1])?)?,
                text_of(a.get(fields[2])?)?,
            ))
        })
        .collect()
}

pub fn from_json(text: &str) -> Option<Parts> {
    let caps = JSON.captures(text)?;
    let env: Value = serde_json::from_str(cap(&caps, 1)).ok()?;
    let items = env.get("items")?.as_array().filter(|i| !i.is_empty())?;
    let tasks = items.iter().map(json_task).collect::<Option<Vec<_>>>()?;
    let phases = match env.get("phases").filter(|v| !v.is_null()) {
        None => Vec::new(),
        Some(list) => list
            .as_array()?
            .iter()
            .map(|p| {
                Some((
                    int_strict(p.get("number")?)?,
                    p.get("name")
                        .map(|n| n.as_str().map_or_else(|| n.to_string(), str::to_string))?,
                ))
            })
            .collect::<Option<Vec<_>>>()?,
    };
    let constraints = env
        .get("constraints")
        .and_then(Value::as_array)
        .map(|c| {
            c.iter()
                .map(|v| v.as_str().map_or_else(|| v.to_string(), str::to_string))
                .collect()
        })
        .unwrap_or_default();
    Some(Parts {
        goal: env
            .get("goal")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        phases,
        tasks,
        constraints,
        assumptions: triples(&env, "assumptions", ["id", "text", "based_on"])?,
        risks: triples(&env, "risks", ["id", "task", "text"])?,
        team: env
            .get("lanes")
            .and_then(|l| l.get("people"))
            .and_then(Value::as_i64),
    })
}

/// The first reader that finds a complete plan in the text, or None.
pub fn read(text: &str) -> Option<Parts> {
    from_table(text)
        .or_else(|| from_file(text))
        .or_else(|| from_json(text))
}

/// The Plan, or None when the parts do not make a valid, acyclic plan.
pub fn build(parts: &Parts, source: &str) -> Option<Plan> {
    let team = parts
        .constraints
        .iter()
        .find_map(|c| TEAM.captures(c).map(|m| num(cap(&m, 1))))
        .filter(|n| *n != 0)
        .or(parts.team);
    let deadline = parts
        .constraints
        .iter()
        .find_map(|c| DEADLINE.captures(c).map(|m| cap(&m, 1).to_string()));
    let ids: HashSet<&str> = parts.tasks.iter().map(|t| t.id.as_str()).collect();
    let tasks: Vec<Task> = parts
        .tasks
        .iter()
        .map(|t| Task {
            depends_on: t
                .depends_on
                .iter()
                .filter(|d| ids.contains(d.as_str()))
                .cloned()
                .collect(),
            ..t.clone()
        })
        .collect();
    if ids.is_empty() || ids.len() != tasks.len() || find_cycle(&deps_of(&tasks)).is_some() {
        return None;
    }
    if tasks.iter().any(|t| units_of(&t.size).is_none()) {
        return None;
    }
    Some(Plan {
        goal: parts.goal.clone(),
        constraints: parts.constraints.clone(),
        deadline: deadline.unwrap_or_default(),
        team_size: team,
        phases: parts
            .phases
            .iter()
            .map(|(n, name)| Phase {
                number: *n,
                name: name.clone(),
            })
            .collect(),
        assumptions: parts
            .assumptions
            .iter()
            .map(|(id, text, based_on)| Assumption {
                id: id.clone(),
                text: text.clone(),
                based_on: based_on.clone(),
            })
            .collect(),
        risks: parts
            .risks
            .iter()
            .filter(|(_, task, _)| ids.contains(task.as_str()))
            .map(|(id, task, text)| Risk {
                id: id.clone(),
                task: task.clone(),
                text: text.clone(),
            })
            .collect(),
        next_id: ids.iter().map(|i| id_key(i).1).max().unwrap_or(0) + 1,
        source: grounding(source, parts),
        granularity: "normal".into(),
        tasks,
        ..Plan::default()
    })
}

/// What a revision checks owners, assumptions and constraints against: the user's words still in the
/// history, plus what the earlier plan already grounded (those words may have been trimmed since).
fn grounding(source: &str, parts: &Parts) -> String {
    let mut pieces: Vec<&str> = vec![source];
    pieces.extend(parts.assumptions.iter().map(|(_, _, b)| b.as_str()));
    pieces.extend(parts.tasks.iter().map(|t| t.owner.as_str()));
    pieces.extend(parts.constraints.iter().map(String::as_str));
    pieces.join("\n")
}
