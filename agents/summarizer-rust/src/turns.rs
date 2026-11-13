//! The turn: intake, then the one route it picked.
//!
//! ```text
//! intake -+- summarize -> render
//!         +- ask (one quote-checked question)
//!         +- fixed reply
//! ```
//!
//! Stateless: everything is built from this turn's input alone. Earlier turns reach this agent only
//! through the platform's history block, which `intake` reads (via `recall`). Nothing pauses: every turn
//! ends with a reply.

use serde_json::Value;

use crate::followup;
use crate::inputs::{Incoming, word_count};
use crate::intake::{self, Plan, Route};
use crate::llm::TurnDeps;
use crate::prompts;
use crate::render;
use crate::smalltalk;
use crate::summarize::{self, Status};

#[derive(Debug, Clone, PartialEq)]
pub struct TurnResult {
    pub reply: String,
    /// This agent sends no data part.
    pub data: Option<Value>,
}

pub async fn turn(incoming: &Incoming, deps: &TurnDeps) -> TurnResult {
    let plan = intake::plan_turn(incoming);
    let reply = match &plan.route {
        Route::Reply(text) => with_notes(text, &plan.notes),
        _ if deps.traceparent.is_none() => prompts::NO_TRACE_CONTEXT.to_string(),
        Route::Summarize => summarize(incoming, deps, &plan).await,
        Route::Ask => {
            let answer = followup::ask(deps, &incoming.text, &plan.source, &incoming.context).await;
            with_notes(&answer, &plan.notes)
        }
    };
    TurnResult { reply, data: None }
}

async fn summarize(incoming: &Incoming, deps: &TurnDeps, plan: &Plan) -> String {
    let result =
        summarize::summarize(deps, &plan.source, &plan.instruction, &incoming.context).await;
    let fixed = match result.status {
        Status::Ok => None,
        Status::OffTopic => Some(smalltalk::OFF_TOPIC_REPLY.as_str()),
        Status::NotSummarizable => Some(prompts::NOT_SUMMARIZABLE),
        Status::Failed => Some(prompts::MODEL_FAILED),
        Status::Unusable => Some(prompts::MODEL_UNUSABLE),
        Status::OutOfTime => Some(prompts::OUT_OF_TIME),
    };
    if let Some(text) = fixed {
        return with_notes(text, &plan.notes);
    }
    let summary = &result.summary;
    if summary.is_empty() {
        let notes: Vec<String> = summary.notes.iter().chain(&plan.notes).cloned().collect();
        return with_notes(prompts::NOTHING_BACKED, &notes);
    }
    let (view, with_sources) = plan.view;
    render::render(
        summary,
        view,
        with_sources,
        word_count(&plan.source),
        result.parts,
        &plan.notes,
    )
}

fn with_notes(text: &str, notes: &[String]) -> String {
    std::iter::once(text)
        .chain(notes.iter().map(String::as_str))
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}
