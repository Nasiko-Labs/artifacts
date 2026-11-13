//! The turn: intake, then the one route it picked.
//!
//! ```text
//! intake -+- process (parse turns, attendees, map/reduce, checks) -> render
//!         +- ask (one quote-checked question)
//!         +- fixed reply
//! ```
//!
//! Stateless: everything is built from this turn's input alone. Earlier turns reach this agent only
//! through the platform's history block, which `intake` reads (via `recall`). Nothing pauses: every turn
//! ends with a reply.

use serde_json::Value;

use crate::attendees::attendees;
use crate::chunking;
use crate::envelope::{self, Meeting};
use crate::followup;
use crate::inputs::{Incoming, title_in, word_count};
use crate::intake::{self, Material, Plan, Route};
use crate::items::{Notes, Turn, UNKNOWN};
use crate::llm::TurnDeps;
use crate::process;
use crate::prompts;
use crate::render;
use crate::transcript;

pub const FIRST_PART_NOTE: &str = "That meeting is long, so I answered from its first part only.";

#[derive(Debug, Clone, PartialEq)]
pub struct TurnResult {
    pub reply: String,
    /// The full envelope (data part) after a processed meeting.
    pub data: Option<Value>,
}

impl TurnResult {
    fn reply(text: String) -> Self {
        Self {
            reply: text,
            data: None,
        }
    }
}

pub async fn turn(incoming: &Incoming, deps: &TurnDeps) -> TurnResult {
    let plan = intake::plan_turn(incoming);
    match &plan.route {
        Route::Reply(text) => TurnResult::reply(with_notes(text, &plan.notes)),
        _ if deps.traceparent.is_none() => TurnResult::reply(prompts::NO_TRACE_CONTEXT.into()),
        Route::Process => process_turn(incoming, deps, &plan).await,
        Route::Ask(material) => TurnResult::reply(ask(incoming, deps, &plan, *material).await),
    }
}

async fn process_turn(incoming: &Incoming, deps: &TurnDeps, plan: &Plan) -> TurnResult {
    let parsed = transcript::parse(&plan.source);
    if parsed.turns.is_empty() {
        return TurnResult::reply(with_notes(prompts::NO_TURNS, &plan.notes));
    }
    let people = attendees(&parsed.turns);
    let names: Vec<String> = people.iter().map(|a| a.name.clone()).collect();
    let outcome = process::run(
        deps,
        &parsed.turns,
        &names,
        &plan.instruction,
        &incoming.context,
    )
    .await;
    if let Some(fixed) = process::fixed_reply(outcome.status) {
        return TurnResult::reply(with_notes(&fixed, &plan.notes));
    }
    if outcome.notes.is_empty() {
        let notes: Vec<String> = outcome
            .notes
            .notes
            .iter()
            .chain(&plan.notes)
            .cloned()
            .collect();
        return TurnResult::reply(with_notes(prompts::NOTHING_BACKED, &notes));
    }
    let title = if parsed.title.is_empty() {
        title_in(&plan.instruction)
    } else {
        parsed.title
    };
    let meeting = Meeting {
        turns: parsed.turns,
        attendees: people,
        has_speakers: parsed.has_speakers,
        title,
        date: parsed.date,
        words: word_count(&plan.source),
        my_name: plan.my_name.clone(),
    };
    present(&outcome.notes, &meeting, plan)
}

fn present(notes: &Notes, meeting: &Meeting, plan: &Plan) -> TurnResult {
    let data = envelope::build(notes, meeting, &render::notes_markdown(notes, meeting));
    let reply = if plan.wants_json {
        render::render_json(&data)
    } else {
        render::render(notes, meeting, plan.view, &plan.notes)
    };
    TurnResult {
        reply,
        data: Some(data),
    }
}

async fn ask(incoming: &Incoming, deps: &TurnDeps, plan: &Plan, material: Material) -> String {
    let mut notes = plan.notes.clone();
    let (turns, name, cite) = match material {
        Material::Notes => (reply_lines(&plan.source), "NOTES", false),
        Material::Nothing => (Vec::new(), "TRANSCRIPT", true),
        Material::Transcript => {
            let turns = transcript::parse(&plan.source).turns;
            let mut chunks = chunking::split(&turns, None);
            if chunks.len() > 1 {
                notes.insert(0, FIRST_PART_NOTE.to_string());
                (chunks.swap_remove(0), "TRANSCRIPT", true)
            } else {
                (turns, "TRANSCRIPT", true)
            }
        }
    };
    let answer = followup::ask(deps, &incoming.text, &turns, name, &incoming.context, cite).await;
    with_notes(&answer, &notes)
}

/// An earlier notes reply as quotable lines (L1..Ln), so an answer is still checked against real text.
pub fn reply_lines(reply: &str) -> Vec<Turn> {
    reply
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .enumerate()
        .map(|(n, line)| Turn::new(&format!("L{}", n + 1), UNKNOWN, "", line))
        .collect()
}

fn with_notes(text: &str, notes: &[String]) -> String {
    std::iter::once(text)
        .chain(notes.iter().map(String::as_str))
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}
