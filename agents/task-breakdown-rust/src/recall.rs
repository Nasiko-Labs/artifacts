//! Pure: the plan this conversation is working on, recovered from the platform's history slice (this agent
//! stores nothing between messages).
//!
//! The newest assistant reply that shows a plan is read back (plan_text.rs). Code-only changes the user
//! asked for after it ("we are 2 people", "T1 is done", "4 units per week") are replayed from the later
//! user turns, since those replies did not repeat the whole plan. If the newest plan reply is cut off,
//! the plan counts as lost (`trimmed`): an older, complete plan is never used in its place, because it
//! would be stale.

use std::sync::LazyLock;

use regex::Regex;

use crate::history::{ASSISTANT, Turn, USER};
use crate::models::Plan;
use crate::plan_text;
use crate::queries;
use crate::util::re;

static PLAN_LIKE: LazyLock<Regex> =
    LazyLock::new(|| re(r#"(?ms)^\*\*Plan: |^# Task plan: |```json\s*\{.*"items""#));
static FOOTER: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?m)^_Draft only:.*$|^\(empty; add dated lines as work happens\)$"));
const REPLAYED: &[&str] = &["team", "rate", "done"];

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Recalled {
    pub plan: Option<Plan>,
    /// A plan reply is in the history but could not be read whole.
    pub trimmed: bool,
}

pub fn looks_like_plan(text: &str) -> bool {
    PLAN_LIKE.is_match(text)
}

pub fn from_history(turns: &[Turn]) -> Recalled {
    let Some(index) = turns
        .iter()
        .rposition(|t| t.role == ASSISTANT && looks_like_plan(&t.text))
    else {
        return Recalled::default();
    };
    let source: Vec<&str> = turns
        .iter()
        .filter(|t| t.role == USER)
        .map(|t| t.text.as_str())
        .collect();
    let turn = &turns[index];
    let parts = if turn.compressed() {
        None
    } else {
        plan_text::read(&turn.text)
    };
    match parts.and_then(|p| plan_text::build(&p, &source.join("\n"))) {
        Some(plan) => Recalled {
            plan: Some(replay(plan, &turns[index + 1..])),
            trimmed: false,
        },
        None => Recalled {
            plan: None,
            trimmed: true,
        },
    }
}

fn replay(plan: Plan, later: &[Turn]) -> Plan {
    later
        .iter()
        .filter(|t| t.role == USER)
        .fold(plan, |plan, turn| {
            match queries::parse(&turn.text).filter(|c| REPLAYED.contains(&c.kind)) {
                Some(command) => queries::with_change(&plan, &command).unwrap_or(plan),
                None => plan,
            }
        })
}

/// A plan the user pasted back into this message (as asked when the history lost it), and the rest of
/// the message (an instruction before the plan, or after its footer). (None, text) when there is none.
pub fn pasted(text: &str) -> (Option<Plan>, String) {
    let Some(found) = PLAN_LIKE.find(text) else {
        return (None, text.to_string());
    };
    let start = found.start();
    let Some(plan) = plan_text::read(&text[start..]).and_then(|p| plan_text::build(&p, text))
    else {
        return (None, text.to_string());
    };
    let after = FOOTER
        .find_iter(text)
        .filter(|m| m.start() >= start)
        .last()
        .map_or(String::new(), |m| {
            text[m.end()..].trim().trim_matches('`').trim().to_string()
        });
    let rest: Vec<&str> = [text[..start].trim(), after.as_str()]
        .into_iter()
        .filter(|p| !p.is_empty())
        .collect();
    (Some(plan), rest.join("\n"))
}
