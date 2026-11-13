//! Pure: decide what one turn does, before any LLM call. Stateless: the plan to work from is the one the
//! user pasted, or the one recovered from the platform's history slice (recall.rs); nothing is stored.
//!
//! ```text
//! greeting / small talk                         -> canned intro (no call)
//! "put this in my tracker"                      -> fixed answer (no call)
//! an envelope or a pasted plan                  -> load it as the plan (no call), or revise it
//! a plan in the history -+- a new goal          -> plan (replaces the old one)
//!                        +- a question / team   -> query (no call)
//!                        +- anything else       -> revise (the model may still say off-topic)
//! no plan -+- a team/timebox change, goal in history -> plan that goal again with the change (rebuilt)
//!          +- a follow-up (question, "split T4") -> "paste the plan again" / "no plan yet" (no call)
//!          +- vague goal that asks for a plan    -> ask for the goal in one line (no call)
//!          +- anything else                      -> plan (the model may say off-topic)
//! ```
//! Nothing ever pauses: every route ends the task with a reply.

use std::sync::LazyLock;

use regex::Regex;

use crate::envelope_in;
use crate::history::{Turn, USER};
use crate::inputs::{
    Brief, Incoming, MAX_GOAL_CHARS, asks_for_plan, deadline, is_replan, is_vague, parse_brief,
    team_size,
};
use crate::models::Plan;
use crate::prompts;
use crate::queries::{self, Command};
use crate::recall;
use crate::smalltalk;
use crate::util::char_len;

static NEW_GOAL: LazyLock<Regex> = LazyLock::new(|| {
    crate::util::re(
        r"(?i)^\s*(?:please\s+)?(?:break\s+(?:down|up)|plan\b|decompose|create (?:a |an )?(?:\w+ )?plan|make (?:a |an )?(?:\w+ ){0,2}plan|new (?:goal|plan)|here(?:'s| is) (?:our|the|my|a)\b|i need (?:a )?plan)",
    )
});
static TASK_ID: LazyLock<Regex> = LazyLock::new(|| crate::util::re(r"\bT\d+\b"));
static PLAN_REF: LazyLock<Regex> =
    LazyLock::new(|| crate::util::re(r"(?i)\b(?:the|this|that|my|your|our) plan\b|\bphase \d+\b"));

/// "make it a 2-week plan", "now cut this to 3 weeks": a change to a plan that came before, not a goal of
/// its own.
static REFERS_BACK: LazyLock<Regex> = LazyLock::new(|| {
    crate::util::re(
        r"(?i)^\s*(?:(?:ok(?:ay)?|now|then|please|and|so)[,\s]+)*(?:make|turn|change|redo|rework|re-?plan|fit|squeeze|adjust|shrink|cut|scale)\s+(?:it|this|that|them)\b",
    )
});

#[derive(Debug, Clone, PartialEq)]
pub enum Route {
    Reply(String),
    /// `replaced`: a plan in the history is replaced by a new goal. `rebuilt`: the plan was not in the
    /// history, so the goal found there is planned again with this turn's team size / timebox.
    Plan {
        brief: Brief,
        replaced: bool,
        rebuilt: bool,
    },
    Revise {
        plan: Plan,
        instruction: String,
    },
    Query {
        plan: Plan,
        command: Command,
    },
    Loaded(Plan),
}

pub fn is_new_goal(text: &str) -> bool {
    char_len(text) > MAX_GOAL_CHARS || (NEW_GOAL.is_match(text) && !TASK_ID.is_match(text))
}

/// A message that only makes sense against an existing plan: a code-answered question (a team size alone
/// may start a new plan), or an edit that names a task, a phase or the plan.
pub fn is_followup(text: &str, command: Option<&Command>) -> bool {
    if is_new_goal(text) {
        return false;
    }
    command.is_some_and(|c| c.kind != "team")
        || TASK_ID.is_match(text)
        || PLAN_REF.is_match(text)
        || is_replan_change(text, command)
}

/// A team size or timebox applied to an earlier plan ("make it a 2-week plan for one engineer", "we are 2
/// people"), as opposed to a new goal that happens to state one.
pub fn is_replan_change(text: &str, command: Option<&Command>) -> bool {
    if is_new_goal(text) || !is_replan(text) {
        return false;
    }
    REFERS_BACK.is_match(text) || command.is_some_and(|c| c.kind == "team")
}

/// The user's goal for the plan under discussion: the newest user turn that opens a plan, else the oldest
/// user turn that reads as a goal (not small talk, not a follow-up, not too vague).
pub fn goal_in_history(turns: &[Turn]) -> Option<String> {
    let users: Vec<&str> = turns
        .iter()
        .filter(|t| t.role == USER && !t.compressed())
        .map(|t| t.text.as_str())
        .collect();
    if let Some(newest) = users.iter().rev().find(|t| is_new_goal(t)) {
        return Some(newest.to_string());
    }
    users
        .into_iter()
        .find(|t| {
            smalltalk::reply_to(t).is_none()
                && !is_followup(t, queries::parse(t).as_ref())
                && !is_vague(&parse_brief(t))
        })
        .map(str::to_string)
}

/// The goal's brief with the follow-up's team size and timebox applied.
pub fn rebuilt_brief(goal: &str, change: &str) -> Brief {
    let base = parse_brief(goal);
    let due = deadline(change);
    Brief {
        team_size: team_size(change).or(base.team_size),
        deadline: if due.is_empty() {
            base.deadline.clone()
        } else {
            due
        },
        ..base
    }
}

fn with_plan(plan: Plan, rest: String) -> Route {
    if rest.is_empty() {
        return Route::Loaded(plan);
    }
    match queries::parse(&rest) {
        Some(command) => Route::Query { plan, command },
        None => Route::Revise {
            plan,
            instruction: rest,
        },
    }
}

pub fn plan_turn(incoming: &Incoming) -> Route {
    let text = incoming.text.as_str();
    if let Some(canned) = smalltalk::reply_to(text) {
        return Route::Reply(canned);
    }
    if let Some((env, rest)) = envelope_in::find(text) {
        return with_plan(envelope_in::to_plan(&env, text), rest);
    }
    if let (Some(plan), rest) = recall::pasted(text) {
        return with_plan(plan, rest);
    }
    let command = queries::parse(text);
    if command.as_ref().is_some_and(|c| c.kind == "tracker") {
        return Route::Reply(prompts::TRACKER.into());
    }
    let recalled = recall::from_history(&incoming.history);
    if let Some(stored) = recalled.plan.clone()
        && !is_new_goal(text)
    {
        return match command {
            Some(command) => Route::Query {
                plan: stored,
                command,
            },
            None => Route::Revise {
                plan: stored,
                instruction: text.to_string(),
            },
        };
    }
    if recalled.plan.is_none()
        && is_replan_change(text, command.as_ref())
        && let Some(goal) = goal_in_history(&incoming.history)
    {
        return Route::Plan {
            brief: rebuilt_brief(&goal, text),
            replaced: false,
            rebuilt: true,
        };
    }
    if recalled.plan.is_none()
        && (is_followup(text, command.as_ref()) || (recalled.trimmed && command.is_some()))
    {
        let reply = if incoming.history.is_empty() {
            prompts::NO_PLAN
        } else {
            prompts::PLAN_NOT_IN_HISTORY
        };
        return Route::Reply(reply.into());
    }
    let brief = parse_brief(text);
    if is_vague(&brief) && asks_for_plan(text) {
        return Route::Reply(prompts::NEED_GOAL.into());
    }
    Route::Plan {
        brief,
        replaced: recalled.plan.is_some(),
        rebuilt: false,
    }
}
