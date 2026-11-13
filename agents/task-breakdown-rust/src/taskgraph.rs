//! Pure graph maths over the task dependencies. The model never orders, counts or adds: this module does.
//!
//! `Deps` lists each task id (in plan order) with the ids whose output it needs. Effort units come from
//! the size label (S/M/L/XL = 1/2/3/5): relative units, never days. Nothing here panics on a cycle: the
//! callers pass acyclic graphs, and a cycle that slips through only yields a partial order.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use regex::Regex;

use crate::util::{num, re};

pub type Deps = [(String, Vec<String>)];
pub type IdKey = (u8, i64, String);

static ID: LazyLock<Regex> = LazyLock::new(|| re(r"^T(\d+)$"));

/// Effort units of a size label; None for an unknown label.
pub fn units_of(size: &str) -> Option<i64> {
    match size {
        "S" => Some(1),
        "M" => Some(2),
        "L" => Some(3),
        "XL" => Some(5),
        _ => None,
    }
}

pub const SIZES: &[(&str, i64)] = &[("S", 1), ("M", 2), ("L", 3), ("XL", 5)];

pub fn id_key(task_id: &str) -> IdKey {
    match ID.captures(task_id) {
        Some(caps) => (
            0,
            num(caps.get(1).map_or("0", |m| m.as_str())),
            task_id.to_string(),
        ),
        None => (1, 0, task_id.to_string()),
    }
}

pub fn ordered<'a>(ids: impl IntoIterator<Item = &'a str>) -> Vec<&'a str> {
    let mut out: Vec<&str> = ids.into_iter().collect();
    out.sort_by_key(|i| id_key(i));
    out
}

fn as_map(deps: &Deps) -> HashMap<&str, Vec<&str>> {
    let mut map: HashMap<&str, Vec<&str>> = HashMap::new();
    for (id, need) in deps {
        map.insert(id.as_str(), need.iter().map(String::as_str).collect());
    }
    map
}

/// A cycle as a path [a, b, ..., a] (a needs b needs ... needs a), or None. Deterministic: ids in order.
pub fn find_cycle(deps: &Deps) -> Option<Vec<String>> {
    let map = as_map(deps);
    let mut state: HashMap<&str, u8> = HashMap::new();
    let mut stack: Vec<&str> = Vec::new();
    for node in ordered(map.keys().copied()) {
        if !state.contains_key(node)
            && let Some(found) = visit(node, &map, &mut state, &mut stack)
        {
            return Some(found);
        }
    }
    None
}

fn visit<'a>(
    node: &'a str,
    map: &HashMap<&'a str, Vec<&'a str>>,
    state: &mut HashMap<&'a str, u8>,
    stack: &mut Vec<&'a str>,
) -> Option<Vec<String>> {
    state.insert(node, 1);
    stack.push(node);
    let needs = map.get(node).cloned().unwrap_or_default();
    for dep in ordered(needs.into_iter().filter(|d| map.contains_key(d))) {
        match state.get(dep) {
            Some(1) => {
                let start = stack.iter().position(|n| *n == dep).unwrap_or(0);
                let mut cycle: Vec<String> = stack[start..].iter().map(|s| s.to_string()).collect();
                cycle.push(dep.to_string());
                return Some(cycle);
            }
            Some(_) => {}
            None => {
                if let Some(found) = visit(dep, map, state, stack) {
                    return Some(found);
                }
            }
        }
    }
    stack.pop();
    state.insert(node, 2);
    None
}

/// Kahn's algorithm, lowest id first among the ready. A leftover cycle is appended in id order.
pub fn topo(deps: &Deps) -> Vec<String> {
    let map = as_map(deps);
    let mut remaining: HashMap<&str, HashSet<&str>> = map
        .iter()
        .map(|(n, need)| {
            (
                *n,
                need.iter()
                    .copied()
                    .filter(|d| map.contains_key(d))
                    .collect(),
            )
        })
        .collect();
    let mut out: Vec<String> = Vec::new();
    while !remaining.is_empty() {
        let ready: Vec<&str> = ordered(
            remaining
                .iter()
                .filter(|(_, need)| need.is_empty())
                .map(|(n, _)| *n),
        );
        if ready.is_empty() {
            out.extend(
                ordered(remaining.keys().copied())
                    .iter()
                    .map(|s| s.to_string()),
            );
            break;
        }
        for node in &ready {
            out.push(node.to_string());
            remaining.remove(node);
        }
        for need in remaining.values_mut() {
            for node in &ready {
                need.remove(node);
            }
        }
    }
    out
}

/// Wave k = tasks whose dependencies are all in waves < k. Wave 1 can start now.
pub fn waves(deps: &Deps) -> HashMap<String, i64> {
    let map = as_map(deps);
    let mut level: HashMap<String, i64> = HashMap::new();
    for node in topo(deps) {
        let before = map
            .get(node.as_str())
            .map(|need| {
                need.iter()
                    .filter(|d| map.contains_key(*d))
                    .map(|d| level.get(*d).copied().unwrap_or(0))
                    .max()
                    .unwrap_or(0)
            })
            .unwrap_or(0);
        level.insert(node, 1 + before);
    }
    level
}

pub fn wave_lists(deps: &Deps) -> Vec<Vec<String>> {
    let level = waves(deps);
    let top = level.values().copied().max().unwrap_or(0);
    (1..=top)
        .map(|k| {
            ordered(
                level
                    .iter()
                    .filter(|(_, w)| **w == k)
                    .map(|(n, _)| n.as_str()),
            )
            .into_iter()
            .map(str::to_string)
            .collect()
        })
        .collect()
}

/// The longest chain by summed units; ties go to the lowest id. Returns (chain start->end, units).
pub fn critical_path(deps: &Deps, units: &HashMap<String, i64>) -> (Vec<String>, i64) {
    let map = as_map(deps);
    let mut best: HashMap<String, i64> = HashMap::new();
    let mut prev: HashMap<String, Option<String>> = HashMap::new();
    for node in topo(deps) {
        let inputs = ordered(
            map.get(node.as_str())
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .filter(|d| map.contains_key(d) && best.contains_key(*d)),
        );
        let pick = inputs
            .into_iter()
            .min_by_key(|d| (-best.get(*d).copied().unwrap_or(0), id_key(d)))
            .map(str::to_string);
        let own = units.get(&node).copied().unwrap_or(0);
        let total = own
            + pick
                .as_ref()
                .and_then(|p| best.get(p).copied())
                .unwrap_or(0);
        best.insert(node.clone(), total);
        prev.insert(node, pick);
    }
    let Some(end) = best
        .iter()
        .min_by_key(|(n, b)| (-**b, id_key(n)))
        .map(|(n, _)| n.clone())
    else {
        return (Vec::new(), 0);
    };
    let total = best.get(&end).copied().unwrap_or(0);
    let mut chain = vec![end];
    while let Some(Some(before)) = chain.last().and_then(|last| prev.get(last)) {
        if chain.contains(before) {
            break;
        }
        chain.push(before.clone());
    }
    chain.reverse();
    (chain, total)
}

/// (direct dependents, every task downstream) of one task.
pub fn dependents(deps: &Deps, task_id: &str) -> (Vec<String>, Vec<String>) {
    let children = |node: &str| -> Vec<String> {
        deps.iter()
            .filter(|(_, need)| need.iter().any(|d| d == node))
            .map(|(n, _)| n.clone())
            .collect()
    };
    let direct: Vec<String> = ordered(children(task_id).iter().map(String::as_str))
        .into_iter()
        .map(str::to_string)
        .collect();
    let mut seen: HashSet<String> = HashSet::new();
    let mut frontier = direct.clone();
    while let Some(node) = frontier.pop() {
        if seen.insert(node.clone()) {
            frontier.extend(children(&node));
        }
    }
    let all = ordered(seen.iter().map(String::as_str))
        .into_iter()
        .map(str::to_string)
        .collect();
    (direct, all)
}

/// Greedy list scheduling for N people by critical-path priority. Returns (per-lane tasks, makespan units).
pub fn lanes(deps: &Deps, units: &HashMap<String, i64>, people: i64) -> (Vec<Vec<String>>, i64) {
    let map = as_map(deps);
    let priority = bottom_levels(deps, units);
    let count = usize::try_from(people.max(1)).unwrap_or(1);
    let mut lane_free = vec![0_i64; count];
    let mut plan: Vec<Vec<String>> = vec![Vec::new(); count];
    let mut finish: HashMap<String, i64> = HashMap::new();
    let mut now = 0_i64;
    let mut left: HashSet<&str> = map.keys().copied().collect();
    while !left.is_empty() {
        let mut ready: Vec<&str> = left
            .iter()
            .copied()
            .filter(|n| {
                map.get(n).is_none_or(|need| {
                    need.iter()
                        .filter(|d| map.contains_key(*d))
                        .all(|d| finish.get(*d).copied().unwrap_or(now + 1) <= now)
                })
            })
            .collect();
        ready.sort_by_key(|n| (-priority.get(*n).copied().unwrap_or(0), id_key(n)));
        let free: Vec<usize> = (0..count).filter(|i| lane_free[*i] <= now).collect();
        for (lane, node) in free.into_iter().zip(ready) {
            let end = now + units.get(node).copied().unwrap_or(0);
            plan[lane].push(node.to_string());
            finish.insert(node.to_string(), end);
            lane_free[lane] = end;
            left.remove(node);
        }
        let future = lane_free
            .iter()
            .chain(finish.values())
            .copied()
            .filter(|t| *t > now)
            .min();
        match future {
            Some(next) => now = next,
            None if !left.is_empty() => break, // a cycle: nothing can ever start
            None => {}
        }
    }
    (plan, finish.values().copied().max().unwrap_or(0))
}

fn bottom_levels(deps: &Deps, units: &HashMap<String, i64>) -> HashMap<String, i64> {
    let mut level: HashMap<String, i64> = HashMap::new();
    for node in topo(deps).into_iter().rev() {
        let below = deps
            .iter()
            .filter(|(_, need)| need.contains(&node))
            .map(|(child, _)| level.get(child).copied().unwrap_or(0))
            .max()
            .unwrap_or(0);
        let own = units.get(&node).copied().unwrap_or(0);
        level.insert(node, own + below);
    }
    level
}
