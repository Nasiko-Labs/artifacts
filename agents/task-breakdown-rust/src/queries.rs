//! Pure: follow-ups answered in code from the recovered plan, with zero LLM calls: the critical path,
//! what can start now, what depends on a task, counts per phase, one task's details, lanes for a team
//! size, weeks at a stated rate, marking a task done, the json envelope, the files, and "put this in my
//! tracker".

use std::sync::LazyLock;

use regex::Regex;

use crate::inputs::{number, number_alt, team_size, word_count};
use crate::models::{Plan, Task};
use crate::util::{first_group, re};

pub const MAX_QUERY_WORDS: usize = 25;
pub const TEAM_ONLY_MAX_WORDS: usize = 8;

static TRACKER: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)\b(?:put|create|file|add|push|open|send|make)\b.*\b(?:jira|linear|asana|trello|clickup|monday|tracker|tickets?|issues?|cards?|board)\b",
    )
});
static PATTERNS: LazyLock<Vec<(&'static str, Regex)>> = LazyLock::new(|| {
    let n = number_alt(3);
    vec![
        (
            "done",
            re(
                r"(?i)\bT(\d+)\s+(?:is|was|has been)\s+(?:done|finished|complete|completed)\b|\bmark\s+T(\d+)\s+(?:as\s+)?(?:done|finished|complete|completed)\b",
            ),
        ),
        (
            "rate",
            re(&format!(
                r"(?i)\b{n}\s+units?\s+(?:per|a|each|every)\s+week\b"
            )),
        ),
        (
            "dependents",
            re(
                r"(?i)\b(?:depends? on|waits? (?:on|for)|blocked by|needs?|after)\s+T(\d+)\b|\bT(\d+)'?s? dependents\b",
            ),
        ),
        (
            "critical",
            re(r"(?i)critical path|longest (?:chain|path)|bottleneck"),
        ),
        (
            "phase_count",
            re(&format!(r"(?i)how many tasks.*\bphase\s+{n}\b")),
        ),
        (
            "count",
            re(r"(?i)how many tasks|how big is (?:the|this) plan"),
        ),
        (
            "start",
            re(
                r"(?i)start (?:now|first|today)|can (?:we|i) start|what(?:'s| is) (?:ready|next)|in parallel|at the same time",
            ),
        ),
        (
            "task",
            re(
                r"(?i)^(?:what(?:'s| is)|show(?: me)?|details? (?:of|for|on)|explain|tell me about)\s+T(\d+)\b",
            ),
        ),
        (
            "file",
            re(
                r"(?i)task_plan(?:\.md)?|plan file|(?:show|give me|repeat) (?:me )?(?:the )?(?:whole |full )?plan",
            ),
        ),
        ("progress", re(r"(?i)progress(?:\.md| log| template| file)")),
    ]
});
static REVISE_START: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)^\s*(?:please\s+)?(?:split|merge|add|drop|remove|delete|rename|move|change|make|replace|combine|insert|re-?scope|shrink|break|reorder|swap|turn|update|cut|rewrite)\b",
    )
});
static JSON_ONLY: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)^\s*(?:(?:show|give me|send|return)\s+)?(?:it|the plan|this)?\s*(?:as json|json only|in json)\s*[.!]?\s*$",
    )
});

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Command {
    pub kind: &'static str,
    pub number: i64,
    pub task: String,
}

impl Command {
    pub fn new(kind: &'static str) -> Self {
        Self {
            kind,
            number: 0,
            task: String::new(),
        }
    }
}

/// The follow-up this message asks for, or None (a revision or a new goal).
pub fn parse(text: &str) -> Option<Command> {
    if TRACKER.is_match(text) {
        return Some(Command::new("tracker"));
    }
    if word_count(text) > MAX_QUERY_WORDS || REVISE_START.is_match(text) {
        return None;
    }
    if JSON_ONLY.is_match(text) {
        return Some(Command::new("json"));
    }
    let people = team_size(text);
    for (kind, rx) in PATTERNS.iter() {
        let Some(caps) = rx.captures(text) else {
            continue;
        };
        let first = first_group(&caps);
        return Some(match *kind {
            "done" | "dependents" | "task" => Command {
                task: format!("T{first}"),
                ..Command::new(kind)
            },
            "rate" | "phase_count" => Command {
                number: number(first),
                ..Command::new(kind)
            },
            "start" if people.is_some() => Command {
                number: people.unwrap_or(1),
                ..Command::new("team")
            },
            _ => Command::new(kind),
        });
    }
    match people {
        Some(n) if word_count(text) <= TEAM_ONLY_MAX_WORDS => Some(Command {
            number: n,
            ..Command::new("team")
        }),
        _ => None,
    }
}

/// The plan a command changes (team size, rate, a task done), or None when it only reads.
pub fn with_change(plan: &Plan, command: &Command) -> Option<Plan> {
    match command.kind {
        "team" => {
            let mut constraints = vec![format!("Team of {}", command.number)];
            constraints.extend(
                plan.constraints
                    .iter()
                    .filter(|c| !c.starts_with("Team of "))
                    .cloned(),
            );
            Some(Plan {
                team_size: Some(command.number),
                constraints,
                ..plan.clone()
            })
        }
        "rate" if command.number > 0 => Some(Plan {
            rate: Some(command.number),
            ..plan.clone()
        }),
        "done" if plan.task(&command.task).is_some() => {
            let tasks = plan
                .tasks
                .iter()
                .map(|t| {
                    if t.id == command.task {
                        Task {
                            status: "done".into(),
                            ..t.clone()
                        }
                    } else {
                        t.clone()
                    }
                })
                .collect();
            Some(Plan {
                tasks,
                ..plan.clone()
            })
        }
        _ => None,
    }
}
