//! The turn: intake, then the one route it picked.
//!
//! ```text
//! intake -+- translate
//!         +- scope (one call: text or off_topic) -+- translate
//!         |                                       +- declined / "which language?"
//!         +- fixed reply / original
//! ```
//!
//! Stateless: everything is built from this turn's input alone. The last job ("now in French", "more
//! formal", "show the original") is rebuilt from the platform's history block by `intake` (via
//! `recall`). Nothing pauses: every turn ends with a reply.

use serde_json::Value;

use crate::envelope::{self, Outcome};
use crate::inputs::Incoming;
use crate::intake::{self, Plan};
use crate::languages;
use crate::llm::{self, LlmError, TurnDeps};
use crate::prompts;
use crate::render;
use crate::request::Job;
use crate::segments;
use crate::smalltalk;
use crate::translate::{self, ChunkStatus};

#[derive(Debug, Clone, PartialEq)]
pub struct TurnResult {
    pub reply: String,
    /// The envelope (plus `translated_envelope`) for the data part.
    pub data: Option<Value>,
}

impl TurnResult {
    fn reply(text: &str) -> Self {
        Self {
            reply: text.to_string(),
            data: None,
        }
    }
}

pub async fn turn(incoming: &Incoming, deps: &TurnDeps) -> TurnResult {
    let plan = intake::plan_turn(incoming);
    if let Plan::Reply(text) = &plan {
        return TurnResult::reply(&join(text, &incoming.notes));
    }
    if deps.traceparent.is_none() {
        return TurnResult::reply(prompts::NO_TRACEPARENT);
    }
    let plan = match plan {
        Plan::Scope(job) => match scope_check(deps, job, incoming).await {
            Ok(next) => next,
            Err(reply) => return reply,
        },
        other => other,
    };
    match plan {
        Plan::Translate { job, notes } => translate(deps, &job, &notes, incoming).await,
        Plan::Reply(text) => TurnResult::reply(&text),
        Plan::Scope(_) => TurnResult::reply(&smalltalk::OFF_TOPIC_REPLY),
    }
}

/// One intake call decides whether a bare message is text to translate; anything unreadable after one
/// retry is declined (fail closed).
async fn scope_check(deps: &TurnDeps, job: Job, incoming: &Incoming) -> Result<Plan, TurnResult> {
    let messages = prompts::scope_messages(&job.source, &incoming.context);
    let mut status = None;
    for _ in 0..2 {
        let raw = deps
            .call(&messages, llm::SCOPE_MAX_TOKENS)
            .await
            .map_err(|e| failed(&e))?;
        status = llm::load(&raw)
            .and_then(|v| v.get("status").and_then(Value::as_str).map(str::to_string))
            .filter(|s| s == "text" || s == "off_topic");
        if status.is_some() {
            break;
        }
    }
    if status.as_deref() != Some("text") {
        return Err(TurnResult::reply(&smalltalk::OFF_TOPIC_REPLY));
    }
    Ok(intake::next_step(job, incoming, false))
}

fn failed(err: &LlmError) -> TurnResult {
    TurnResult::reply(match err {
        LlmError::Transport(_) => prompts::MODEL_FAILED,
        LlmError::BadResponse(_) => prompts::MODEL_UNUSABLE,
        LlmError::NoTraceparent => prompts::NO_TRACEPARENT,
        LlmError::OutOfTime | LlmError::BudgetSpent => prompts::OUT_OF_TIME,
    })
}

async fn translate(
    deps: &TurnDeps,
    job: &Job,
    notes: &[String],
    incoming: &Incoming,
) -> TurnResult {
    let prepared = translate::prepare(job);
    let results = translate::run(deps, job, &prepared, &incoming.context).await;
    let chunks = segments::chunk(&prepared.segments);
    match envelope::build(job, &prepared, &chunks, &results) {
        Outcome::Failed(why) => TurnResult::reply(match why {
            ChunkStatus::Unreachable => prompts::MODEL_FAILED,
            ChunkStatus::Unusable => prompts::MODEL_UNUSABLE,
            _ => prompts::OUT_OF_TIME,
        }),
        Outcome::Already => {
            let original = render::original(&job.source, job.envelope.as_ref());
            TurnResult::reply(&prompts::already_in(
                &languages::name_of(&job.target),
                &original,
            ))
        }
        Outcome::Ok {
            envelope,
            translated_input,
        } => {
            let data = render::data(&envelope, translated_input.as_ref());
            let reply = render::reply(&envelope, translated_input.as_ref(), job.as_json);
            let all: Vec<String> = notes.iter().chain(&incoming.notes).cloned().collect();
            TurnResult {
                reply: join(&reply, &all),
                data: Some(Value::Object(data)),
            }
        }
    }
}

fn join(text: &str, notes: &[String]) -> String {
    std::iter::once(text)
        .chain(notes.iter().map(String::as_str))
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}
