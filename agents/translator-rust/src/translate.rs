//! One translation: mask -> split -> translate chunks (<= 4 at once) -> verify -> one repair per failing
//! chunk -> code fixes -> restore. Each chunk call gets the previous chunk's last segment as read-only
//! context.

use std::collections::HashMap;

use futures::StreamExt as _;
use serde_json::Value;

use crate::languages;
use crate::llm::{self, LlmError, TurnDeps};
use crate::mask;
use crate::prompts::{self, TranslateRequest};
use crate::register;
use crate::request::{ENVELOPE_FIELDS, Job};
use crate::segments::{self, Segment};
use crate::verify;

pub const CONCURRENCY: usize = 4;

/// The masked job: segments in order, the token -> original map, and the docs they came from.
#[derive(Debug, Clone)]
pub struct Prepared {
    pub segments: Vec<Segment>,
    pub spans: HashMap<String, String>,
    /// (envelope field or None, original text)
    pub docs: Vec<(Option<String>, String)>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChunkStatus {
    Ok,
    /// The model could not be reached.
    Unreachable,
    /// The model answered, but not usably, even after the repair.
    Unusable,
    /// No time left to start it.
    Skipped,
}

#[derive(Debug, Clone)]
pub struct ChunkResult {
    pub status: ChunkStatus,
    pub translations: Vec<String>,
    pub source_language: String,
    /// Raw model notes.
    pub notes: Vec<Value>,
    pub code_notes: Vec<String>,
    pub repaired: bool,
    pub digits_left: bool,
    pub structure_fixed: bool,
}

impl ChunkResult {
    fn status(status: ChunkStatus) -> Self {
        Self {
            status,
            translations: Vec::new(),
            source_language: String::new(),
            notes: Vec::new(),
            code_notes: Vec::new(),
            repaired: false,
            digits_left: false,
            structure_fixed: false,
        }
    }
}

pub fn prepare(job: &Job) -> Prepared {
    let docs: Vec<(Option<String>, String)> = match &job.envelope {
        Some(envelope) => ENVELOPE_FIELDS
            .iter()
            .filter_map(|f| {
                let text = envelope.get(*f)?.as_str()?;
                (!text.trim().is_empty()).then(|| (Some(f.to_string()), text.to_string()))
            })
            .collect(),
        None => vec![(None, job.source.clone())],
    };
    let mut segments = Vec::new();
    let mut spans = HashMap::new();
    for (name, text) in &docs {
        let (masked, found) = mask::mask(text, &job.keep, spans.len() + 1);
        spans.extend(found);
        segments.extend(segments::split(&masked, name.as_deref()));
    }
    Prepared {
        segments,
        spans,
        docs,
    }
}

pub async fn run(
    deps: &TurnDeps,
    job: &Job,
    prepared: &Prepared,
    context: &[String],
) -> Vec<ChunkResult> {
    let chunks = segments::chunk(&prepared.segments);
    let calls: Vec<_> = chunks
        .iter()
        .enumerate()
        .map(|(index, ids)| {
            let previous = index
                .checked_sub(1)
                .and_then(|p| chunks[p].last())
                .map_or(String::new(), |&i| prepared.segments[i].text.clone());
            let sources: Vec<String> = ids
                .iter()
                .map(|&i| prepared.segments[i].text.clone())
                .collect();
            let first = ids.first().map_or(1, |i| i + 1);
            Box::pin(async move {
                let result = chunk(
                    deps,
                    job,
                    &sources,
                    first,
                    &previous,
                    &prepared.spans,
                    context,
                )
                .await;
                (index, result)
            })
        })
        .collect();
    let mut results: Vec<(usize, ChunkResult)> = futures::stream::iter(calls)
        .buffer_unordered(CONCURRENCY)
        .collect()
        .await;
    results.sort_by_key(|(i, _)| *i);
    results.into_iter().map(|(_, r)| r).collect()
}

fn failure(err: &LlmError) -> ChunkStatus {
    match err {
        LlmError::Transport(_) => ChunkStatus::Unreachable,
        LlmError::BadResponse(_) => ChunkStatus::Unusable,
        LlmError::NoTraceparent | LlmError::OutOfTime | LlmError::BudgetSpent => {
            ChunkStatus::Skipped
        }
    }
}

async fn chunk(
    deps: &TurnDeps,
    job: &Job,
    sources: &[String],
    first: usize,
    previous: &str,
    spans: &HashMap<String, String>,
    context: &[String],
) -> ChunkResult {
    if !deps.has_time() {
        return ChunkResult::status(ChunkStatus::Skipped);
    }
    let target = languages::prompt_name(&job.target);
    let rule = register::rule(&languages::base(&job.target), job.register);
    let mut glossary: Vec<String> = job
        .keep
        .iter()
        .map(|k| format!("keep '{k}' untranslated"))
        .collect();
    glossary.extend(
        job.glossary
            .iter()
            .map(|(a, b)| format!("translate '{a}' as '{b}'")),
    );
    let messages = prompts::translate_messages(&TranslateRequest {
        target: &target,
        register_rule: &rule,
        segments: sources,
        previous,
        glossary: &glossary,
        source_hint: &job.source_hint,
        context,
    });
    let raw = match deps.call(&messages, llm::MAX_TOKENS).await {
        Ok(raw) => raw,
        Err(err) => return ChunkResult::status(failure(&err)),
    };
    let mut parsed = parse(&raw, sources.len());
    let problems = problems(parsed.as_ref(), sources, first, job);
    if problems.is_empty()
        && let Some(parsed) = parsed.take()
    {
        return finish(parsed, sources, first, spans, false);
    }
    let mut repaired = None;
    if deps.has_time() {
        let retry = prompts::repair_messages(&messages, &raw, &problems);
        if let Ok(again) = deps.call(&retry, llm::MAX_TOKENS).await {
            repaired = parse(&again, sources.len());
        }
    }
    let was_repaired = repaired.is_some();
    match repaired.or(parsed) {
        Some(best) => finish(best, sources, first, spans, was_repaired),
        None => ChunkResult::status(ChunkStatus::Unusable),
    }
}

/// The model's chunk answer: its segments (token spellings normalised), notes and source language.
pub struct Parsed {
    pub segments: Vec<String>,
    pub notes: Vec<Value>,
    pub source_language: String,
}

pub fn parse(raw: &str, n: usize) -> Option<Parsed> {
    let data = llm::load(raw)?;
    let object = data.as_object()?;
    let out = object.get("segments")?.as_array()?;
    if out.len() != n {
        return None;
    }
    let segments = out
        .iter()
        .map(|s| s.as_str().map(mask::normalize_tokens))
        .collect::<Option<Vec<_>>>()?;
    let notes = object
        .get("notes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let source_language = match object.get("source_language") {
        Some(Value::String(s)) => s.trim().to_string(),
        _ => String::new(),
    };
    Some(Parsed {
        segments,
        notes,
        source_language,
    })
}

fn problems(parsed: Option<&Parsed>, sources: &[String], first: usize, job: &Job) -> Vec<String> {
    let Some(parsed) = parsed else {
        return vec![format!(
            "the answer must be one JSON object whose \"segments\" list has exactly {} strings",
            sources.len()
        )];
    };
    let mut found = verify::chunk_problems(sources, &parsed.segments, first);
    let same_language = languages::base(&parsed.source_language) == languages::base(&job.target);
    if !same_language && verify::unchanged(sources, &parsed.segments) {
        found.push(format!(
            "the segments were returned untranslated; translate them into {}",
            job.target
        ));
    }
    found
}

fn finish(
    parsed: Parsed,
    sources: &[String],
    first: usize,
    spans: &HashMap<String, String>,
    repaired: bool,
) -> ChunkResult {
    let mut texts = Vec::new();
    let mut code_notes = Vec::new();
    let mut fixed_structure = false;
    for (offset, (src, out)) in sources.iter().zip(&parsed.segments).enumerate() {
        let (mut placed, notes) = verify::place_tokens(src, out, spans, first + offset);
        if segments::shape(src) != segments::shape(&placed) {
            let (restored, changed) = segments::restore_markers(src, &placed);
            placed = restored;
            fixed_structure =
                fixed_structure || changed || segments::shape(src) != segments::shape(&placed);
        }
        texts.push(placed);
        code_notes.extend(notes);
    }
    let last = first + sources.len().saturating_sub(1);
    let digits_left = verify::digit_problem(&sources.join("\n"), &texts.join("\n")).is_some();
    if digits_left {
        code_notes.push(format!(
            "Numbers differ from the source in segments {first}-{last}; check them."
        ));
    }
    if fixed_structure {
        code_notes.push(format!(
            "List or heading markers were restored in segments {first}-{last}."
        ));
    }
    ChunkResult {
        status: ChunkStatus::Ok,
        translations: texts,
        source_language: parsed.source_language,
        notes: parsed.notes,
        code_notes,
        repaired,
        digits_left,
        structure_fixed: fixed_structure,
    }
}
