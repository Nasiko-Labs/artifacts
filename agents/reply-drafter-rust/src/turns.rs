//! One turn, stateless.
//!
//! ```text
//! request -+- greeting / small talk (no call) ---------------------------------------------> reply
//!          +- plan (code) -+- fixed reply (tone saved, envelope re-render, off-topic question,
//!                          |               "what should it say?", "paste it again") --------> reply
//!                          +- compose: draft(1) -> check(code) -> [repair(1)] -> render ----> reply
//! ```
//!
//! Nothing is kept between messages: the starting state (measured style, the incoming message, the user's
//! words, the current reply) is rebuilt for each turn from the platform's history block (`recall`), so
//! any replica can answer any message. Nothing ever pauses: every turn ends with a reply.

use serde_json::Value;

use crate::drafting::{self, Composed};
use crate::envelope as env;
use crate::inputs::{self, Request};
use crate::llm::{LlmError, TurnDeps};
use crate::plan::{Plan, plan_turn};
use crate::prompts;
use crate::recall;
use crate::render;
use crate::smalltalk;

#[derive(Debug, Clone, PartialEq)]
pub struct TurnResult {
    pub reply: String,
    /// This turn's envelope, for the data part.
    pub data: Option<Value>,
}

impl TurnResult {
    fn text(reply: &str) -> Self {
        Self {
            reply: reply.to_string(),
            data: None,
        }
    }
}

pub async fn turn(request: &Request, deps: &TurnDeps) -> TurnResult {
    if let Some(canned) = smalltalk::reply_to(&request.text) {
        return TurnResult::text(&canned); // a greeting or small talk: no LLM call
    }
    let state = recall::state_from(&request.history);
    let (job, plan_notes) = match plan_turn(&state, request) {
        Plan::Fixed(reply) => return TurnResult::text(&reply),
        Plan::Compose { job, notes } => (job, notes),
    };
    if deps.traceparent.is_none() {
        return TurnResult::text(prompts::NO_TRACE_CONTEXT);
    }
    let wants_json = request.wants_json || inputs::wants_json(&request.text);
    let composed = match drafting::compose(deps, &job).await {
        Ok(composed) => composed,
        Err(err) => return TurnResult::text(failure_text(&err)),
    };
    let (reply, notes, code_fixed, ungrounded) = match composed {
        Composed::OffTopic => return TurnResult::text(&smalltalk::OFF_TOPIC_REPLY),
        Composed::NeedsInfo if job.incoming.is_none() && job.current.is_none() => {
            return TurnResult::text(prompts::NEED_INCOMING);
        }
        Composed::NeedsInfo | Composed::Failed => {
            return TurnResult::text(prompts::COULD_NOT_DRAFT);
        }
        Composed::Ok {
            reply,
            notes,
            code_fixed,
            ungrounded_after_repair,
        } => (reply, notes, code_fixed, ungrounded_after_repair),
    };
    let target = drafting::target(&job);
    let tone = env::tone_notes(&job.metrics, &target, &reply);
    let level = env::confidence(&job.metrics, &reply, &target, code_fixed, ungrounded);
    let data = env::build(
        &reply,
        &job.channel,
        &target.sender,
        &job.metrics,
        &tone,
        level,
    );
    let mut extra: Vec<String> = plan_notes.into_iter().chain(notes).collect();
    if !job.metrics.measured() {
        extra.push(render::EXAMPLES_HINT.into());
    }
    let out = if wants_json {
        env::fenced(&data)
    } else {
        render::reply_text(&data, &job.metrics, &extra)
    };
    TurnResult {
        reply: out,
        data: Some(data),
    }
}

/// An accurate sentence for a call that produced no answer: "couldn't reach" only for transport failures.
fn failure_text(err: &LlmError) -> &'static str {
    match err {
        LlmError::Transport(_) => prompts::MODEL_FAILED,
        LlmError::BadResponse(_) => prompts::MODEL_UNUSABLE,
        LlmError::NoTraceparent => prompts::NO_TRACE_CONTEXT,
        LlmError::OutOfTime | LlmError::BudgetSpent => prompts::BUDGET_SPENT,
    }
}
