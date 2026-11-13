//! The turn: intake, then the one route it picked.
//!
//! ```text
//! intake -+- plan   (map chunks -> plan -> repair, in planning.rs) -+
//!         +- revise (revise -> reconcile ids -> repair -> fit) -----+-> render
//!         +- loaded (an envelope or a pasted plan) -----------------+
//!         +- rebuilt (plan the goal from history again, then fit the change) -> render
//!         +- query  (answered in code from the recovered plan)
//!         +- fixed reply
//! ```
//!
//! Stateless: every step works on this turn's input only. The plan a follow-up works on is the one
//! recovered from the platform's history slice (recall.rs), so any replica can answer any message.
//! Nothing pauses: every turn ends with a reply.

use serde_json::Value;

use crate::answers;
use crate::envelope;
use crate::fit;
use crate::inputs::Incoming;
use crate::intake::{self, Route};
use crate::llm::TurnDeps;
use crate::models::Plan;
use crate::planning::{self, Outcome};
use crate::prompts;
use crate::queries;
use crate::render;
use crate::smalltalk;

pub const REPLACED: &str = "_This replaces the earlier plan in this conversation._";
pub const REBUILT: &str = concat!(
    "_The earlier plan wasn't in the conversation history I received, so I rebuilt it from your goal with ",
    "this change._"
);

/// The line above a rendered plan, if any.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Lead {
    None,
    Replaced,
    Rebuilt,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TurnResult {
    pub reply: String,
    /// The envelope, attached as a data part.
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

pub async fn turn(incoming: &Incoming, deps: &TurnDeps) -> TurnResult {
    match intake::plan_turn(incoming) {
        Route::Reply(reply) => TurnResult::text(&reply),
        Route::Loaded(plan) => rendered(&plan, incoming, Lead::None),
        Route::Query { plan, command } => {
            let changed = queries::with_change(&plan, &command);
            let (reply, data) = answers::answer(
                changed.as_ref().unwrap_or(&plan),
                &command,
                incoming.wants_json,
            );
            TurnResult { reply, data }
        }
        Route::Plan { .. } | Route::Revise { .. } if deps.traceparent.is_none() => {
            TurnResult::text(prompts::NO_TRACEPARENT)
        }
        Route::Plan {
            brief,
            replaced,
            rebuilt,
        } => {
            let outcome = planning::new_plan(deps, &brief, &incoming.policy).await;
            if rebuilt {
                // The change (team size / timebox) is applied to the rebuilt plan as to a revision.
                let fitted = match outcome {
                    Outcome::Ok(plan) => Outcome::Ok(Box::new(fit::fit(*plan))),
                    other => other,
                };
                return finish(fitted, incoming, Lead::Rebuilt);
            }
            finish(
                outcome,
                incoming,
                if replaced { Lead::Replaced } else { Lead::None },
            )
        }
        Route::Revise { plan, instruction } => {
            let outcome = planning::revise(deps, &plan, &instruction, &incoming.policy).await;
            finish(outcome, incoming, Lead::None)
        }
    }
}

fn finish(outcome: Outcome, incoming: &Incoming, lead: Lead) -> TurnResult {
    match outcome {
        Outcome::Ok(plan) => rendered(&plan, incoming, lead),
        Outcome::OffTopic => TurnResult::text(&smalltalk::OFF_TOPIC_REPLY),
        Outcome::NeedsInfo => TurnResult::text(prompts::STILL_VAGUE),
        Outcome::Failed => TurnResult::text(prompts::MODEL_FAILED),
        Outcome::Unusable => TurnResult::text(prompts::MODEL_UNUSABLE),
    }
}

fn rendered(plan: &Plan, incoming: &Incoming, lead: Lead) -> TurnResult {
    let env = envelope::build(plan);
    let lead: &[&str] = match lead {
        Lead::None => &[],
        Lead::Replaced => &[REPLACED],
        Lead::Rebuilt => &[REBUILT],
    };
    let reply = if incoming.wants_json {
        render::render_json(&env)
    } else {
        render::render(plan, &env, lead)
    };
    TurnResult {
        reply,
        data: Some(env),
    }
}
