//! Pure: every number the reply shows, computed once from the checked plan.

use std::collections::{BTreeMap, HashMap, HashSet};

use crate::checks::deps_of;
use crate::models::Plan;
use crate::taskgraph::{critical_path, lanes, units_of, waves};
use crate::util::round_half_even;

#[derive(Debug, Clone, PartialEq)]
pub struct Metrics {
    pub units: HashMap<String, i64>,
    pub wave_of: HashMap<String, i64>,
    pub waves: Vec<Vec<String>>,
    pub critical: Vec<String>,
    pub critical_units: i64,
    pub total_units: i64,
    /// phase -> (tasks, units), in plan phase order.
    pub phase_counts: BTreeMap<i64, (i64, i64)>,
}

impl Metrics {
    /// The critical path as a whole percent of all effort.
    pub fn critical_share(&self) -> i64 {
        if self.total_units == 0 {
            return 0;
        }
        round_half_even(100.0 * self.critical_units as f64 / self.total_units as f64)
    }

    /// Pending tasks whose dependencies are all done (at creation: wave 1).
    pub fn can_start(&self, plan: &Plan) -> Vec<String> {
        let done: HashSet<&str> = plan
            .tasks
            .iter()
            .filter(|t| t.is_done())
            .map(|t| t.id.as_str())
            .collect();
        plan.tasks
            .iter()
            .filter(|t| !t.is_done() && t.depends_on.iter().all(|d| done.contains(d.as_str())))
            .map(|t| t.id.clone())
            .collect()
    }

    pub fn units_of(&self, task_id: &str) -> i64 {
        self.units.get(task_id).copied().unwrap_or(0)
    }

    pub fn phase_count(&self, phase: i64) -> (i64, i64) {
        self.phase_counts.get(&phase).copied().unwrap_or((0, 0))
    }
}

pub fn unit_map(plan: &Plan) -> HashMap<String, i64> {
    plan.tasks
        .iter()
        .map(|t| (t.id.clone(), units_of(&t.size).unwrap_or(0)))
        .collect()
}

pub fn compute(plan: &Plan) -> Metrics {
    let deps = deps_of(&plan.tasks);
    let units = unit_map(plan);
    let wave_of = waves(&deps);
    let (critical, critical_units) = critical_path(&deps, &units);
    let top = wave_of.values().copied().max().unwrap_or(0);
    let wave_lists = (1..=top)
        .map(|k| {
            plan.tasks
                .iter()
                .filter(|t| wave_of.get(&t.id) == Some(&k))
                .map(|t| t.id.clone())
                .collect()
        })
        .collect();
    let phase_counts = plan
        .phases
        .iter()
        .map(|p| {
            let in_phase = plan.tasks.iter().filter(|t| t.phase == p.number);
            let count = in_phase.clone().count() as i64;
            let sum = in_phase
                .map(|t| units.get(&t.id).copied().unwrap_or(0))
                .sum();
            (p.number, (count, sum))
        })
        .collect();
    Metrics {
        total_units: plan
            .tasks
            .iter()
            .map(|t| units.get(&t.id).copied().unwrap_or(0))
            .sum(),
        units,
        wave_of,
        waves: wave_lists,
        critical,
        critical_units,
        phase_counts,
    }
}

pub fn team_lanes(plan: &Plan, people: i64) -> (Vec<Vec<String>>, i64) {
    lanes(&deps_of(&plan.tasks), &unit_map(plan), people)
}

/// Weeks at least, at `rate` units per week: the critical path cannot be shortened by more people.
pub fn weeks_needed(critical_units: i64, rate: i64) -> i64 {
    if rate > 0 {
        (critical_units + rate - 1).div_euclid(rate)
    } else {
        0
    }
}
