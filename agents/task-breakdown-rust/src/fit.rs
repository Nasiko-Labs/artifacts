//! Pure: fit a plan to a timebox ("a 2-week plan for one engineer"), in code, from the size labels.
//!
//! Capacity = weeks x the user's stated rate (units per week), else weeks x DEFAULT_UNITS_PER_PERSON_WEEK x
//! people, and the reply says which. While the plan is over capacity, the lowest-priority task that nothing
//! kept still needs is deferred: the latest phase first, then off the critical path, then the largest, then
//! the highest id. Done and org-rule tasks are never deferred. The kept tasks are sequenced for the team by
//! the lanes code.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use regex::Regex;

use crate::checks::deps_of;
use crate::inputs::{number, number_alt};
use crate::models::{Plan, Task};
use crate::taskgraph::{critical_path, id_key, units_of};
use crate::util::{cap, re};

/// Assumed (1 unit ~ a focused day) when the user gave no rate; stated in the reply.
pub const DEFAULT_UNITS_PER_PERSON_WEEK: i64 = 5;

static TIMEBOX: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)\b{}[- ]?(day|week|sprint|month)s?\b",
        number_alt(3)
    ))
});

fn weeks_per(unit: &str) -> f64 {
    match unit {
        "day" => 1.0 / 5.0,
        "sprint" => 2.0,
        "month" => 4.0,
        _ => 1.0,
    }
}

/// (weeks, the timebox as a label like "2 weeks"), or None when the text holds no countable timebox.
pub fn timebox(text: &str) -> Option<(f64, String)> {
    let caps = TIMEBOX.captures(text)?;
    let count = number(cap(&caps, 1));
    let unit = cap(&caps, 2).to_lowercase();
    if count <= 0 {
        return None;
    }
    let plural = if count == 1 { "" } else { "s" };
    Some((
        count as f64 * weeks_per(&unit),
        format!("{count} {unit}{plural}"),
    ))
}

/// The plan cut to its timebox (unchanged when it has none).
pub fn fit(plan: Plan) -> Plan {
    let Some((weeks, label)) = timebox(&plan.deadline) else {
        return plan;
    };
    let people = plan.team_size.unwrap_or(1).max(1);
    let (weekly, basis) = match plan.rate {
        Some(rate) if rate > 0 => (rate, format!("{label} at your {rate} units per week")),
        _ => {
            let who = if people == 1 {
                "1 person".to_string()
            } else {
                format!("{people} people")
            };
            (
                DEFAULT_UNITS_PER_PERSON_WEEK * people,
                format!(
                    "{label} x {who} x {DEFAULT_UNITS_PER_PERSON_WEEK} units per person-week, assumed; say \"4 units per week\" to change it"
                ),
            )
        }
    };
    let capacity = (weeks * weekly as f64).floor() as i64;
    let (kept, deferred) = cut(&plan.tasks, capacity);
    let ids: HashSet<&str> = kept.iter().map(|t| t.id.as_str()).collect();
    let mut notes = plan.notes.clone();
    let over = kept
        .iter()
        .map(|t| units_of(&t.size).unwrap_or(0))
        .sum::<i64>()
        - capacity;
    if over > 0 {
        notes.push(format!(
            "Still {over} units over {label}: the remaining tasks are needed by others or done."
        ));
    }
    let phases = plan
        .phases
        .iter()
        .filter(|p| kept.iter().any(|t| t.phase == p.number))
        .cloned()
        .collect();
    let risks = plan
        .risks
        .iter()
        .filter(|r| ids.contains(r.task.as_str()))
        .cloned()
        .collect();
    let all_deferred = plan.deferred.iter().cloned().chain(deferred).collect();
    Plan {
        tasks: kept,
        deferred: all_deferred,
        capacity_units: Some(capacity),
        timebox_label: label,
        fit_basis: basis,
        notes,
        phases,
        risks,
        ..plan
    }
}

fn cut(tasks: &[Task], capacity: i64) -> (Vec<Task>, Vec<Task>) {
    let units: HashMap<&str, i64> = tasks
        .iter()
        .map(|t| (t.id.as_str(), units_of(&t.size).unwrap_or(0)))
        .collect();
    let owned_units: HashMap<String, i64> =
        units.iter().map(|(k, v)| (k.to_string(), *v)).collect();
    let (chain, _) = critical_path(&deps_of(tasks), &owned_units);
    let critical: HashSet<&str> = chain.iter().map(String::as_str).collect();
    let mut kept: Vec<Task> = tasks.to_vec();
    let mut deferred: Vec<Task> = Vec::new();
    let unit = |t: &Task| units.get(t.id.as_str()).copied().unwrap_or(0);
    while kept.iter().map(unit).sum::<i64>() > capacity {
        let needed: HashSet<&str> = kept
            .iter()
            .flat_map(|t| t.depends_on.iter().map(String::as_str))
            .collect();
        let last = kept
            .iter()
            .enumerate()
            .filter(|(_, t)| !needed.contains(t.id.as_str()) && !t.is_done() && !t.is_policy())
            .max_by_key(|(_, t)| {
                (
                    t.phase,
                    !critical.contains(t.id.as_str()),
                    unit(t),
                    id_key(&t.id),
                )
            })
            .map(|(i, _)| i);
        let Some(index) = last else { break };
        deferred.push(kept.remove(index));
    }
    deferred.sort_by_key(|t| id_key(&t.id));
    (kept, deferred)
}
