//! Summarize one source: a single pass up to SINGLE_PASS_MAX_CHARS, map-reduce above it (`mapreduce.rs`).

use std::time::Duration;

use crate::checks;
use crate::chunking;
use crate::items::{Item, Summary};
use crate::llm::{self, LlmError, TurnDeps};
use crate::mapreduce;
use crate::prompts;
use crate::util::char_len;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok,
    OffTopic,
    NotSummarizable,
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
    pub summary: Summary,
    /// Map-reduce: every checked item of every finished part.
    pub pool: Vec<Item>,
    pub parts: usize,
}

impl Outcome {
    fn status(status: Status, parts: usize) -> Self {
        Self {
            status,
            summary: Summary::default(),
            pool: Vec::new(),
            parts,
        }
    }
}

fn failure(err: &LlmError) -> Status {
    match err {
        LlmError::Transport(_) => Status::Failed,
        LlmError::BadResponse(_) | LlmError::Contract => Status::Unusable,
        LlmError::NoTraceparent | LlmError::OutOfTime | LlmError::BudgetSpent => Status::OutOfTime,
    }
}

pub async fn summarize(
    deps: &TurnDeps,
    source: &str,
    instruction: &str,
    context: &[String],
) -> Outcome {
    summarize_with(
        deps,
        source,
        instruction,
        context,
        mapreduce::REDUCE_RESERVE,
    )
    .await
}

/// `summarize` with the time kept back for the reduce step given explicitly.
pub async fn summarize_with(
    deps: &TurnDeps,
    source: &str,
    instruction: &str,
    context: &[String],
    reduce_reserve: Duration,
) -> Outcome {
    if char_len(source) > chunking::SINGLE_PASS_MAX_CHARS {
        return map_reduce(deps, source, instruction, context, reduce_reserve).await;
    }
    let messages = prompts::summarize_messages(source, instruction, context, None);
    let parsed = deps
        .complete_json(
            &messages,
            prompts::SUMMARY_CONTRACT,
            checks::parse_summary,
            llm::MAX_TOKENS,
        )
        .await;
    let parsed = match parsed {
        Ok(parsed) => parsed,
        Err(err) => {
            tracing::warn!("single-pass summary failed: {err:?}");
            return Outcome::status(failure(&err), 1);
        }
    };
    match parsed.status.as_str() {
        "off_topic" => Outcome::status(Status::OffTopic, 1),
        "not_summarizable" => Outcome::status(Status::NotSummarizable, 1),
        _ => Outcome {
            status: Status::Ok,
            summary: checks::checked_summary(&parsed, source, Vec::new()),
            pool: Vec::new(),
            parts: 1,
        },
    }
}

async fn map_reduce(
    deps: &TurnDeps,
    source: &str,
    instruction: &str,
    context: &[String],
    reduce_reserve: Duration,
) -> Outcome {
    let chunks = chunking::split(source, None);
    let mapped = mapreduce::map_chunks(deps, &chunks, instruction, context, reduce_reserve).await;
    if mapped.parts.is_empty() {
        let status = if mapped.off_topic {
            Status::OffTopic
        } else if mapped.unreachable > 0 {
            Status::Failed
        } else if mapped.unusable > 0 {
            Status::Unusable
        } else if mapped.not_summarizable > 0 {
            Status::NotSummarizable
        } else {
            Status::OutOfTime
        };
        return Outcome::status(status, chunks.len());
    }
    let summary = mapreduce::reduce(deps, &mapped, source, instruction, context).await;
    Outcome {
        status: Status::Ok,
        summary,
        pool: mapped.pool(),
        parts: chunks.len(),
    }
}
