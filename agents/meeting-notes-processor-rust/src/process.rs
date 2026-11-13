//! Process one parsed meeting: a single call up to SINGLE_PASS_MAX_CHARS, map-reduce above it.

use std::time::Duration;

use crate::checks::{self, Index};
use crate::chunking;
use crate::items::{Notes, Turn};
use crate::llm::{self, LlmError, TurnDeps};
use crate::mapreduce;
use crate::prompts;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok,
    OffTopic,
    NotMeeting,
    /// The model could not be reached (transport or HTTP failure).
    Failed,
    /// The model answered, but not in the contract, even after one repair.
    Unusable,
    /// The turn ran out of time or calls.
    OutOfTime,
}

#[derive(Debug, Clone)]
pub struct Outcome {
    pub status: Status,
    pub notes: Notes,
    pub chunked: bool,
}

impl Outcome {
    fn status(status: Status, chunked: bool) -> Self {
        Self {
            status,
            notes: Notes::default(),
            chunked,
        }
    }
}

pub fn failure(err: &LlmError) -> Status {
    match err {
        LlmError::Transport(_) => Status::Failed,
        LlmError::BadResponse(_) | LlmError::Contract => Status::Unusable,
        LlmError::NoTraceparent | LlmError::OutOfTime | LlmError::BudgetSpent => Status::OutOfTime,
    }
}

/// The fixed reply for a status that is not `Ok`.
pub fn fixed_reply(status: Status) -> Option<String> {
    let text = match status {
        Status::Ok => return None,
        Status::OffTopic => crate::smalltalk::OFF_TOPIC_REPLY.as_str(),
        Status::NotMeeting => prompts::NOT_MEETING,
        Status::Failed => prompts::MODEL_FAILED,
        Status::Unusable => prompts::MODEL_UNUSABLE,
        Status::OutOfTime => prompts::OUT_OF_TIME,
    };
    Some(text.to_string())
}

pub async fn run(
    deps: &TurnDeps,
    turns: &[Turn],
    speakers: &[String],
    instruction: &str,
    context: &[String],
) -> Outcome {
    run_with(
        deps,
        turns,
        speakers,
        instruction,
        context,
        mapreduce::REDUCE_RESERVE,
    )
    .await
}

/// `run` with the time kept back for the reduce step given explicitly.
pub async fn run_with(
    deps: &TurnDeps,
    turns: &[Turn],
    speakers: &[String],
    instruction: &str,
    context: &[String],
    reduce_reserve: Duration,
) -> Outcome {
    let index = Index::new(turns, speakers);
    let chunks = chunking::split(turns, None);
    if chunks.len() > 1 {
        return map_reduce(deps, &chunks, &index, instruction, context, reduce_reserve).await;
    }
    let messages = prompts::notes_messages(&chunking::as_text(turns), instruction, context, None);
    let parsed = deps
        .complete_json(
            &messages,
            prompts::NOTES_CONTRACT,
            checks::parse_notes,
            llm::MAX_TOKENS,
        )
        .await;
    let parsed = match parsed {
        Ok(parsed) => parsed,
        Err(err) => {
            tracing::warn!("single-pass notes failed: {err:?}");
            return Outcome::status(failure(&err), false);
        }
    };
    match parsed.status.as_str() {
        "off_topic" => return Outcome::status(Status::OffTopic, false),
        "not_meeting" => return Outcome::status(Status::NotMeeting, false),
        _ => {}
    }
    let checked = checks::check_all(&parsed, &index, "");
    let kept = checked.kept();
    let (topics, decisions, actions, capped) =
        checks::finalize(checked.topics, checked.decisions, checked.actions);
    Outcome {
        status: Status::Ok,
        notes: Notes {
            topics,
            decisions,
            actions,
            kept_before_caps: kept,
            dropped: checked.dropped,
            capped,
            ..Notes::default()
        },
        chunked: false,
    }
}

async fn map_reduce(
    deps: &TurnDeps,
    chunks: &[Vec<Turn>],
    index: &Index,
    instruction: &str,
    context: &[String],
    reduce_reserve: Duration,
) -> Outcome {
    let mapped =
        mapreduce::map_chunks(deps, chunks, index, instruction, context, reduce_reserve).await;
    if mapped.parts.is_empty() {
        let status = match mapped.shared.as_str() {
            "off_topic" => Status::OffTopic,
            "not_meeting" => Status::NotMeeting,
            _ if mapped.unreachable > 0 => Status::Failed,
            _ if mapped.unusable > 0 => Status::Unusable,
            _ if mapped.any_status => Status::NotMeeting,
            _ => Status::OutOfTime,
        };
        return Outcome::status(status, true);
    }
    let notes = mapreduce::reduce(deps, &mapped, index, instruction, context).await;
    Outcome {
        status: Status::Ok,
        notes,
        chunked: true,
    }
}
