//! Map-reduce for long meetings.
//!
//! Map: one call per chunk (concurrency MAP_CONCURRENCY); each answer is checked against the parsed turns
//! and its items get ids like c3.a2. Parts still running when the map window closes are cancelled and the
//! reply says how much was covered. Reduce: one call that may only select, merge and order ids and name the
//! merged topics; every quote comes from a chosen id, so the reduce step cannot add an item. If it fails,
//! the union of the checked map items is shown instead.

use std::collections::HashMap;
use std::time::Duration;

use futures::StreamExt as _;
use serde_json::{Map, Value};

use crate::checks::{self, Checked, Index};
use crate::chunking;
use crate::items::{Action, Bullet, Decision, Notes, Topic, Turn};
use crate::llm::{self, LlmError, TurnDeps};
use crate::prompts;
use crate::util::char_len;

pub const MAP_CONCURRENCY: usize = 4;
/// The map window closes this long before the turn deadline, so the reduce has room.
pub const REDUCE_RESERVE: Duration = Duration::from_secs(100);
pub const FALLBACK_NOTE: &str = "I couldn't merge the parts, so the notes are listed part by part.";

#[derive(Debug, Clone, Default)]
pub struct Mapped {
    pub chunks: Vec<Vec<Turn>>,
    /// (chunk index, checked items) of every part that was processed.
    pub parts: Vec<(usize, Checked)>,
    pub timed_out: bool,
    /// Parts the model could not be reached for.
    pub unreachable: usize,
    /// Parts whose answers were not usable (or that ran out of calls).
    pub unusable: usize,
    /// The shared non-ok status ("off_topic" / "not_meeting") when every part returned it.
    pub shared: String,
    /// Some part answered off_topic or not_meeting.
    pub any_status: bool,
}

impl Mapped {
    pub fn failed(&self) -> usize {
        self.unreachable + self.unusable
    }

    pub fn dropped(&self) -> usize {
        self.parts.iter().map(|(_, c)| c.dropped).sum()
    }

    pub fn coverage(&self) -> f64 {
        let sizes: Vec<usize> = self
            .chunks
            .iter()
            .map(|c| char_len(&chunking::as_text(c)))
            .collect();
        let covered: usize = self
            .parts
            .iter()
            .map(|(i, _)| sizes.get(*i).copied().unwrap_or(0))
            .sum();
        covered as f64 / sizes.iter().sum::<usize>().max(1) as f64
    }

    pub fn notes(&self) -> Vec<String> {
        let mut notes = Vec::new();
        if self.timed_out {
            notes.push(coverage_note(self));
        }
        if self.failed() > 0 {
            let why = if self.unreachable > 0 {
                "the model call failed"
            } else {
                "the model's answer was unusable"
            };
            notes.push(format!(
                "{} of {} parts couldn't be processed ({why}).",
                self.failed(),
                self.chunks.len()
            ));
        }
        notes
    }
}

pub fn coverage_note(mapped: &Mapped) -> String {
    let done: Vec<usize> = mapped.parts.iter().map(|(i, _)| *i).collect();
    let mut prefix = 0;
    while done.contains(&prefix) {
        prefix += 1;
    }
    let first = mapped.chunks.first().and_then(|c| c.first());
    let covered_last = prefix
        .checked_sub(1)
        .and_then(|p| mapped.chunks.get(p))
        .and_then(|c| c.last());
    let last = mapped.chunks.iter().rev().find_map(|c| c.last());
    if let (true, Some(a), Some(b), Some(c)) = (prefix > 0, first, covered_last, last)
        && !a.time.is_empty()
        && !b.time.is_empty()
        && !c.time.is_empty()
    {
        return format!(
            "Covered {} to {} of {} before my time limit.",
            a.time, b.time, c.time
        );
    }
    let share = (100.0 * mapped.coverage()).round() as i64;
    format!(
        "Covered {} of {} parts (about {share}%) before my time limit.",
        done.len(),
        mapped.chunks.len()
    )
}

enum PartResult {
    Ok(Checked),
    Status(String),
    Failed(LlmError),
}

pub async fn map_chunks(
    deps: &TurnDeps,
    chunks: &[Vec<Turn>],
    index: &Index,
    instruction: &str,
    context: &[String],
    reduce_reserve: Duration,
) -> Mapped {
    let total = chunks.len();
    let parts: Vec<_> = chunks
        .iter()
        .enumerate()
        .map(|(i, chunk)| {
            Box::pin(map_one(
                deps,
                (i, total),
                chunk,
                index,
                instruction,
                context,
            ))
        })
        .collect();
    let mut stream = futures::stream::iter(parts).buffer_unordered(MAP_CONCURRENCY);
    let window = deps.remaining().saturating_sub(reduce_reserve);
    let closes = tokio::time::sleep(window);
    tokio::pin!(closes);
    let mut results = Vec::new();
    let mut timed_out = false;
    loop {
        tokio::select! {
            next = stream.next() => match next {
                Some(result) => results.push(result),
                None => break,
            },
            () = &mut closes => {
                timed_out = true;
                break;
            }
        }
    }
    drop(stream); // cancels the parts still running
    results.sort_by_key(|(i, _)| *i);
    collect(chunks, results, timed_out)
}

async fn map_one(
    deps: &TurnDeps,
    (index, total): (usize, usize),
    chunk: &[Turn],
    turns: &Index,
    instruction: &str,
    context: &[String],
) -> (usize, PartResult) {
    let messages = prompts::notes_messages(
        &chunking::as_text(chunk),
        instruction,
        context,
        Some((index + 1, total)),
    );
    let result = deps
        .complete_json(
            &messages,
            prompts::NOTES_CONTRACT,
            checks::parse_notes,
            llm::MAX_TOKENS,
        )
        .await;
    let outcome = match result {
        Err(err) => {
            tracing::warn!("map call for part {} failed: {err:?}", index + 1);
            PartResult::Failed(err)
        }
        Ok(parsed) if parsed.status != "ok" => PartResult::Status(parsed.status),
        Ok(parsed) => PartResult::Ok(checks::check_all(
            &parsed,
            turns,
            &format!("c{}.", index + 1),
        )),
    };
    (index, outcome)
}

fn collect(chunks: &[Vec<Turn>], results: Vec<(usize, PartResult)>, timed_out: bool) -> Mapped {
    let mut mapped = Mapped {
        chunks: chunks.to_vec(),
        timed_out,
        ..Mapped::default()
    };
    let mut statuses: Vec<String> = Vec::new();
    for (index, result) in results {
        match result {
            PartResult::Ok(checked) => {
                statuses.push("ok".into());
                mapped.parts.push((index, checked));
            }
            PartResult::Status(status) => statuses.push(status),
            PartResult::Failed(err) => {
                statuses.push("failed".into());
                if err.is_transport() {
                    mapped.unreachable += 1;
                } else {
                    mapped.unusable += 1;
                }
            }
        }
    }
    mapped.any_status = statuses
        .iter()
        .any(|s| s == "off_topic" || s == "not_meeting");
    if let Some(first) = statuses.first()
        && (first == "off_topic" || first == "not_meeting")
        && statuses.iter().all(|s| s == first)
    {
        mapped.shared = first.clone();
    }
    mapped
}

pub fn items_block(mapped: &Mapped) -> String {
    let mut lines = Vec::new();
    for (_, part) in &mapped.parts {
        for topic in &part.topics {
            lines.push(format!("{} [topic] {}", topic.item_id, topic.title));
            lines.extend(
                topic
                    .bullets
                    .iter()
                    .map(|b| format!("  {} {}", b.item_id, b.text)),
            );
        }
        lines.extend(
            part.decisions
                .iter()
                .map(|d| format!("{} [decision] {}", d.item_id, d.text)),
        );
        lines.extend(part.actions.iter().map(|a| {
            let due = if a.due.is_empty() {
                "not stated"
            } else {
                &a.due
            };
            format!(
                "{} [{}] {} (owner: {}; due: {due})",
                a.item_id,
                a.kind.as_str(),
                a.task,
                a.owner
            )
        }));
    }
    lines.join("\n")
}

#[derive(Debug, Clone, PartialEq)]
pub struct Reduced {
    pub topics: Vec<Map<String, Value>>,
    pub decisions: Vec<String>,
    pub actions: Vec<String>,
}

pub fn parse_reduce(data: &Value) -> Option<Reduced> {
    let object = data.as_object()?;
    let topics = object
        .get("topics")?
        .as_array()?
        .iter()
        .map(|t| t.as_object().cloned())
        .collect::<Option<Vec<_>>>()?;
    let ids = |key: &str| -> Option<Vec<String>> {
        match object.get(key) {
            None | Some(Value::Null) => Some(Vec::new()),
            Some(Value::Array(items)) => Some(
                items
                    .iter()
                    .map(|x| x.as_str().map_or_else(|| x.to_string(), str::to_string))
                    .collect(),
            ),
            Some(_) => None,
        }
    };
    Some(Reduced {
        topics,
        decisions: ids("decisions")?,
        actions: ids("actions")?,
    })
}

pub async fn reduce(
    deps: &TurnDeps,
    mapped: &Mapped,
    index: &Index,
    instruction: &str,
    context: &[String],
) -> Notes {
    let messages = prompts::reduce_messages(
        &items_block(mapped),
        instruction,
        mapped.chunks.len(),
        context,
    );
    let parsed = deps
        .complete_json(
            &messages,
            prompts::REDUCE_CONTRACT,
            parse_reduce,
            llm::REDUCE_MAX_TOKENS,
        )
        .await;
    match parsed {
        Ok(parsed) => apply_reduce(&parsed, mapped, index),
        Err(err) => {
            tracing::warn!("reduce failed ({err:?}); showing the union of the checked map items");
            union(mapped, &[FALLBACK_NOTE.to_string()])
        }
    }
}

pub fn apply_reduce(parsed: &Reduced, mapped: &Mapped, index: &Index) -> Notes {
    let all = || mapped.parts.iter().map(|(_, c)| c);
    let bullets: HashMap<&str, (&Bullet, &Topic)> = all()
        .flat_map(|c| &c.topics)
        .flat_map(|t| t.bullets.iter().map(move |b| (b.item_id.as_str(), (b, t))))
        .collect();
    let decisions: HashMap<&str, &Decision> = all()
        .flat_map(|c| &c.decisions)
        .map(|d| (d.item_id.as_str(), d))
        .collect();
    let actions: HashMap<&str, &Action> = all()
        .flat_map(|c| &c.actions)
        .map(|a| (a.item_id.as_str(), a))
        .collect();
    let mut topics = Vec::new();
    for (n, entry) in parsed.topics.iter().enumerate() {
        let ids: Vec<String> = entry
            .get("bullet_ids")
            .and_then(Value::as_array)
            .map(|ids| {
                ids.iter()
                    .map(|v| v.as_str().map_or_else(|| v.to_string(), str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        let chosen: Vec<(&Bullet, &Topic)> = ids
            .iter()
            .filter_map(|i| bullets.get(i.as_str()).copied())
            .collect();
        let Some((_, first_topic)) = chosen.first() else {
            continue;
        };
        let mut title = checks::grounded_text(entry.get("title"), checks::TITLE_WORDS, index);
        if title.is_empty() {
            title = first_topic.title.clone();
        }
        let mut unique = checks::dedup(
            chosen.iter().map(|(b, _)| (*b).clone()).collect(),
            |b| &b.text,
            |_| "",
        );
        unique.truncate(checks::MAX_BULLETS);
        topics.push(Topic {
            title,
            bullets: unique,
            item_id: format!("r.t{}", n + 1),
        });
    }
    if topics.is_empty() {
        topics = all().flat_map(|c| c.topics.clone()).collect();
    }
    let picked_d = parsed
        .decisions
        .iter()
        .filter_map(|i| decisions.get(i.as_str()).map(|d| (*d).clone()))
        .collect();
    let picked_a = parsed
        .actions
        .iter()
        .filter_map(|i| actions.get(i.as_str()).map(|a| (*a).clone()))
        .collect();
    result(mapped, topics, picked_d, picked_a, mapped.notes())
}

pub fn union(mapped: &Mapped, extra: &[String]) -> Notes {
    let all = || mapped.parts.iter().map(|(_, c)| c);
    let mut notes = mapped.notes();
    notes.extend(extra.iter().cloned());
    result(
        mapped,
        all().flat_map(|c| c.topics.clone()).collect(),
        all().flat_map(|c| c.decisions.clone()).collect(),
        all().flat_map(|c| c.actions.clone()).collect(),
        notes,
    )
}

fn result(
    mapped: &Mapped,
    topics: Vec<Topic>,
    decisions: Vec<Decision>,
    actions: Vec<Action>,
    notes: Vec<String>,
) -> Notes {
    let kept = mapped.parts.iter().map(|(_, c)| c.kept()).sum();
    let (topics, decisions, actions, capped) = checks::finalize(topics, decisions, actions);
    Notes {
        topics,
        decisions,
        actions,
        kept_before_caps: kept,
        dropped: mapped.dropped(),
        capped,
        failed_parts: mapped.failed(),
        total_parts: mapped.chunks.len(),
        coverage: mapped.coverage(),
        notes,
    }
}
