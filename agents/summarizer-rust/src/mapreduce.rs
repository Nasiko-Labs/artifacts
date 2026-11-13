//! Map-reduce for long sources.
//!
//! Map: one call per chunk (concurrency MAP_CONCURRENCY), each chunk's items checked against THAT chunk
//! and given ids like c3.a2. Parts still running when the map window closes are cancelled and the reply
//! says how much was covered. Reduce: one call that may only select, merge and order ids and write the
//! TL;DR; snippets come from the chosen ids, so the reduce step cannot introduce a quote. If it fails,
//! the union of the checked map items is shown instead.

use std::collections::HashMap;
use std::time::Duration;

use futures::StreamExt as _;
use serde_json::{Map, Value};

use crate::checks::{self, Checked};
use crate::grounding as g;
use crate::items::{Item, Kind, NOT_STATED, Summary};
use crate::llm::{self, LlmError, TurnDeps};
use crate::prompts;

pub const MAP_CONCURRENCY: usize = 4;
/// The map window closes this long before the turn deadline, so the reduce has room.
pub const REDUCE_RESERVE: Duration = Duration::from_secs(100);
pub const FALLBACK_NOTE: &str =
    "I couldn't merge the parts, so this lists the checked items part by part.";

#[derive(Debug, Clone, Default)]
pub struct Mapped {
    pub total: usize,
    /// (chunk index, [tldr, decisions, actions]) of every part that was summarized.
    pub parts: Vec<(usize, [Checked; 3])>,
    pub sizes: Vec<usize>,
    pub timed_out: bool,
    /// Parts the model could not be reached for.
    pub unreachable: usize,
    /// Parts whose answers were not usable (or that ran out of calls).
    pub unusable: usize,
    pub off_topic: bool,
    /// Parts the model called "not prose".
    pub not_summarizable: usize,
}

impl Mapped {
    pub fn pool(&self) -> Vec<Item> {
        self.parts
            .iter()
            .flat_map(|(_, sections)| sections.iter().flat_map(|c| c.items.iter().cloned()))
            .collect()
    }

    pub fn dropped(&self) -> usize {
        self.parts
            .iter()
            .flat_map(|(_, sections)| sections.iter().map(|c| c.dropped))
            .sum()
    }

    pub fn notes(&self) -> Vec<String> {
        let mut notes = Vec::new();
        if self.timed_out {
            let covered: usize = self
                .parts
                .iter()
                .map(|(i, _)| self.sizes.get(*i).copied().unwrap_or(0))
                .sum();
            let share =
                (100.0 * covered as f64 / self.sizes.iter().sum::<usize>().max(1) as f64).round();
            notes.push(format!(
                "Covered {} of {} parts (about {share}%) before my time limit.",
                self.parts.len(),
                self.total
            ));
        }
        let failed = self.unreachable + self.unusable;
        if failed > 0 {
            let why = if self.unreachable > 0 {
                "the model call failed"
            } else {
                "the model's answer was unusable"
            };
            notes.push(format!(
                "{failed} of {} parts couldn't be summarized ({why}).",
                self.total
            ));
        }
        notes
    }
}

enum PartResult {
    Ok([Checked; 3]),
    Status(String),
    Failed(LlmError),
}

pub async fn map_chunks(
    deps: &TurnDeps,
    chunks: &[String],
    instruction: &str,
    context: &[String],
    reduce_reserve: Duration,
) -> Mapped {
    let total = chunks.len();
    let parts: Vec<_> = chunks
        .iter()
        .enumerate()
        .map(|(i, chunk)| Box::pin(map_one(deps, (i, total), chunk, instruction, context)))
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
    collect(total, chunks, results, timed_out)
}

async fn map_one(
    deps: &TurnDeps,
    (index, total): (usize, usize),
    chunk: &str,
    instruction: &str,
    context: &[String],
) -> (usize, PartResult) {
    let messages =
        prompts::summarize_messages(chunk, instruction, context, Some((index + 1, total)));
    let result = deps
        .complete_json(
            &messages,
            prompts::SUMMARY_CONTRACT,
            checks::parse_summary,
            llm::MAX_TOKENS,
        )
        .await;
    let outcome = match result {
        Err(err) => {
            tracing::warn!("map call for part {} failed: {err:?}", index + 1);
            PartResult::Failed(err)
        }
        Ok(parsed) if parsed.status != "ok" => PartResult::Status(parsed.status),
        Ok(parsed) => PartResult::Ok(checks::check_summary(
            &parsed,
            chunk,
            &format!("c{}.", index + 1),
        )),
    };
    (index, outcome)
}

fn collect(
    total: usize,
    chunks: &[String],
    results: Vec<(usize, PartResult)>,
    timed_out: bool,
) -> Mapped {
    let mut mapped = Mapped {
        total,
        sizes: chunks.iter().map(|c| c.chars().count()).collect(),
        timed_out,
        ..Mapped::default()
    };
    let mut statuses = Vec::new();
    for (index, result) in results {
        match result {
            PartResult::Ok(sections) => {
                statuses.push("ok".to_string());
                mapped.parts.push((index, sections));
            }
            PartResult::Status(status) => statuses.push(status),
            PartResult::Failed(err) => {
                statuses.push("failed".to_string());
                if err.is_transport() {
                    mapped.unreachable += 1;
                } else {
                    mapped.unusable += 1;
                }
            }
        }
    }
    mapped.not_summarizable = statuses.iter().filter(|s| *s == "not_summarizable").count();
    mapped.off_topic = !statuses.is_empty() && statuses.iter().all(|s| s == "off_topic");
    mapped
}

pub async fn reduce(
    deps: &TurnDeps,
    mapped: &Mapped,
    source: &str,
    instruction: &str,
    context: &[String],
) -> Summary {
    let messages = prompts::reduce_messages(
        &items_block(&mapped.pool()),
        instruction,
        mapped.total,
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
        Ok(parsed) => apply_reduce(&parsed, mapped, source),
        Err(err) => {
            tracing::warn!("reduce failed ({err:?}); showing the union of the checked map items");
            union(mapped)
        }
    }
}

pub fn items_block(pool: &[Item]) -> String {
    pool.iter()
        .map(|item| {
            let extra = if item.kind == Kind::Action {
                let due = if item.due.is_empty() {
                    "not stated"
                } else {
                    &item.due
                };
                format!(" (owner: {}; due: {due})", item.owner)
            } else {
                String::new()
            };
            format!(
                "{} [{}] {}{extra}",
                item.item_id,
                item.kind.as_str(),
                item.text
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[derive(Debug, Clone, PartialEq)]
pub struct Reduced {
    pub tldr: Vec<Map<String, Value>>,
    pub decisions: Vec<String>,
    pub actions: Vec<Map<String, Value>>,
}

pub fn parse_reduce(data: &Value) -> Option<Reduced> {
    let object = data.as_object()?;
    let tldr = object.get("tldr")?.as_array()?;
    let list = |key: &str| match object.get(key) {
        None | Some(Value::Null) => Some(Vec::new()),
        Some(Value::Array(items)) => Some(items.clone()),
        Some(_) => None,
    };
    let decisions = list("decisions")?;
    let actions = list("actions")?;
    let maps = |items: &[Value]| {
        items
            .iter()
            .map(|x| x.as_object().cloned())
            .collect::<Option<Vec<_>>>()
    };
    Some(Reduced {
        tldr: maps(tldr)?,
        decisions: decisions
            .iter()
            .map(|x| checks::value_text(Some(x)))
            .collect(),
        actions: maps(&actions)?,
    })
}

pub fn apply_reduce(parsed: &Reduced, mapped: &Mapped, source: &str) -> Summary {
    let pool = mapped.pool();
    let by_id: HashMap<&str, &Item> = pool.iter().map(|i| (i.item_id.as_str(), i)).collect();
    let norm = g::normalise(source);
    let mut dropped = mapped.dropped();
    let mut tldr = Vec::new();
    for bullet in &parsed.tldr {
        match bullet_item(bullet, &by_id, source, &norm) {
            Some(item) => tldr.push(item),
            None => dropped += 1,
        }
    }
    if tldr.is_empty() {
        // nothing the reduce wrote could be backed: fall back to the parts' own TL;DRs
        tldr = pool
            .iter()
            .filter(|i| i.kind == Kind::Tldr)
            .cloned()
            .collect();
    }
    let decisions = parsed
        .decisions
        .iter()
        .filter_map(|id| by_id.get(id.as_str()).filter(|i| i.kind == Kind::Decision))
        .map(|i| (*i).clone())
        .collect();
    let actions = parsed
        .actions
        .iter()
        .filter_map(|entry| action_item(entry, &by_id, source, &norm))
        .collect();
    checks::finalize(tldr, decisions, actions, dropped, mapped.notes())
}

pub fn union(mapped: &Mapped) -> Summary {
    let pool = mapped.pool();
    let of = |kind: Kind| {
        pool.iter()
            .filter(|i| i.kind == kind)
            .cloned()
            .collect::<Vec<_>>()
    };
    let mut notes = mapped.notes();
    notes.push(FALLBACK_NOTE.to_string());
    checks::finalize(
        of(Kind::Tldr),
        of(Kind::Decision),
        of(Kind::Action),
        mapped.dropped(),
        notes,
    )
}

fn known<'a>(
    ids: Option<&Value>,
    by_id: &HashMap<&str, &'a Item>,
    kind: Option<Kind>,
) -> Vec<&'a Item> {
    let Some(ids) = ids.and_then(Value::as_array) else {
        return Vec::new();
    };
    ids.iter()
        .filter_map(Value::as_str)
        .filter_map(|id| by_id.get(id).copied())
        .filter(|i| kind.is_none_or(|k| i.kind == k))
        .collect()
}

fn bullet_item(
    bullet: &Map<String, Value>,
    by_id: &HashMap<&str, &Item>,
    source: &str,
    norm: &str,
) -> Option<Item> {
    let cited = known(bullet.get("item_ids"), by_id, None);
    let text = checks::clean_text(&checks::value_text(bullet.get("text")), source);
    let first = cited.first()?;
    if text.is_empty() || !g::ungrounded_tokens(&text, norm).is_empty() {
        return None;
    }
    Some(Item {
        kind: Kind::Tldr,
        text: checks::cut_words(&text, checks::word_cap(Kind::Tldr)),
        snippet: first.snippet.clone(),
        owner: String::new(),
        due: String::new(),
        item_id: cited
            .iter()
            .map(|i| i.item_id.as_str())
            .collect::<Vec<_>>()
            .join(","),
    })
}

fn action_item(
    entry: &Map<String, Value>,
    by_id: &HashMap<&str, &Item>,
    source: &str,
    norm: &str,
) -> Option<Item> {
    let cited = known(entry.get("ids"), by_id, Some(Kind::Action));
    let base = *cited.first()?;
    let mut text = checks::clean_text(&checks::value_text(entry.get("text")), source);
    if text.is_empty() || !g::ungrounded_tokens(&text, norm).is_empty() {
        text = base.text.clone(); // the merged wording added something the source doesn't say
    }
    let span = g::locate(&base.snippet, norm);
    let (mut owner, mut due) = (base.owner.clone(), base.due.clone());
    if let Some(span) = span {
        if owner == NOT_STATED {
            let named = checks::clean_text(&checks::value_text(entry.get("owner")), source);
            owner = checks::grounded_owner(&named, norm, span);
        }
        if due.is_empty() {
            let when = checks::clean_text(&checks::value_text(entry.get("due")), source);
            due = checks::grounded_due(&when, norm, span);
        }
    }
    Some(Item {
        kind: Kind::Action,
        text: checks::cut_words(&text, checks::word_cap(Kind::Action)),
        snippet: base.snippet.clone(),
        owner,
        due,
        item_id: cited
            .iter()
            .map(|i| i.item_id.as_str())
            .collect::<Vec<_>>()
            .join(","),
    })
}
