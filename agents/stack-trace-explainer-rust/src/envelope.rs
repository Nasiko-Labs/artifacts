//! Pure: the reply envelope (`summary`, `items`, `confidence`, `needs_human` + the four catalog fields
//! mirrored from the primary item). `confidence` and `needs_human` are computed here from the checks,
//! never from the model.

use serde::Serialize;
use serde_json::{Value, json};

use crate::answer::{Evidence, NeedCode};
use crate::explain::{Explained, Status};
use crate::trace_model::Origin;
use crate::trace_parse::has_error_text;
use crate::util::take_chars;

const LEVELS: [&str; 3] = ["low", "medium", "high"];
const NEED_CODE_WINDOW: i64 = 12;
const TRACE_IN_ITEM_CHARS: usize = 6_000;

#[derive(Debug, Clone, Serialize)]
pub struct LocationDetail {
    pub file: String,
    pub line: i64,
    pub function: Option<String>,
    pub origin: String,
    pub from: String,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct ItemChecks {
    pub quotes_removed: usize,
    pub sentences_removed: usize,
    pub fix_code_dropped: bool,
    pub repairs: usize,
    pub repaired_problems: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct Item {
    pub trace_index: usize,
    pub exception: String,
    pub message: String,
    pub language: String,
    pub likely_cause: String,
    pub location: String,
    pub location_detail: Option<LocationDetail>,
    pub fix_suggestion: String,
    pub fix_code: Option<String>,
    pub how_to_verify: Vec<String>,
    pub evidence: Vec<Evidence>,
    pub chain: Vec<String>,
    pub need_code: Option<NeedCode>,
    pub frame_reason: Option<String>,
    pub confidence_notes: Option<String>,
    pub confidence: String,
    pub needs_human: bool,
    pub checks: ItemChecks,
    pub trace: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Envelope {
    pub summary: String,
    pub items: Vec<Item>,
    pub likely_cause: String,
    pub location: String,
    pub fix_suggestion: String,
    pub how_to_verify: Vec<String>,
    pub confidence: String,
    pub needs_human: bool,
    pub skipped_traces: usize,
    pub unexplained_traces: Vec<usize>,
}

impl Envelope {
    pub fn to_value(&self) -> Value {
        serde_json::to_value(self).unwrap_or(Value::Null)
    }
}

pub fn confidence(ex: &Explained) -> &'static str {
    let Some(checked) = ex.checked.as_ref().filter(|_| !ex.trace.frameless) else {
        return "low";
    };
    let mut level: usize = 1;
    // A chain with app frames in several blocks is NOT capped at medium once pasted code verifies
    // the candidate (the demo upgrades the Java chain to high after the follow-up paste).
    if ex.view().is_some()
        && checked.code_quotes >= 1
        && ex.repairs == 0
        && ex.candidate.origin == Origin::App
    {
        level = 2;
    }
    if checked.quotes_kept == 0 {
        level = level.saturating_sub(1); // nothing from the paste backs the cause: one level down
    }
    LEVELS[level]
}

/// The model's request when it passed the check; else, when no pasted code covers an app candidate,
/// a window around its line computed here.
pub fn need_code(ex: &Explained) -> Option<NeedCode> {
    let frame = ex.candidate.frame.as_ref()?;
    if ex.view().is_some() || ex.candidate.origin != Origin::App {
        return None;
    }
    if let Some(asked) = ex.checked.as_ref().and_then(|c| c.need_code.clone()) {
        return Some(asked);
    }
    let first = (frame.line - NEED_CODE_WINDOW).max(1);
    Some(NeedCode {
        file: frame.file.clone(),
        lines: format!("{first}-{}", frame.line + NEED_CODE_WINDOW),
    })
}

pub fn location_detail(ex: &Explained) -> Option<LocationDetail> {
    let frame = ex.candidate.frame.as_ref()?;
    Some(LocationDetail {
        file: frame.file.clone(),
        line: frame.line,
        function: Some(frame.func.clone()).filter(|f| !f.is_empty()),
        origin: ex.candidate.origin.as_str().to_string(),
        from: if ex.view().is_some() { "code" } else { "trace" }.to_string(),
    })
}

pub fn item(index: usize, ex: &Explained) -> Item {
    let trace = &ex.trace;
    let level = confidence(ex);
    let need = need_code(ex);
    let checked = ex.checked.as_ref();
    let result = checked.map(|c| &c.result);
    let text = |f: fn(&crate::answer::Answer) -> &Option<String>| {
        result.and_then(|r| f(r).clone()).unwrap_or_default()
    };
    Item {
        trace_index: index,
        exception: trace.exception_type().to_string(),
        message: trace.message().to_string(),
        language: trace.language.clone(),
        likely_cause: text(|r| &r.likely_cause),
        location: ex
            .candidate
            .frame
            .as_ref()
            .map_or("unknown".to_string(), |f| f.label()),
        location_detail: location_detail(ex),
        fix_suggestion: text(|r| &r.fix_suggestion),
        fix_code: result.and_then(|r| r.fix_code.clone()),
        how_to_verify: result.map(|r| r.how_to_verify.clone()).unwrap_or_default(),
        evidence: result.map(|r| r.evidence.clone()).unwrap_or_default(),
        chain: trace.chain(),
        needs_human: level == "low" || need.is_some(),
        need_code: need,
        frame_reason: if ex.moved {
            result.and_then(|r| r.frame_reason.clone())
        } else {
            None
        },
        confidence_notes: result.and_then(|r| r.confidence_notes.clone()),
        confidence: level.to_string(),
        checks: ItemChecks {
            quotes_removed: checked.map_or(0, |c| c.quotes_removed),
            sentences_removed: checked.map_or(0, |c| c.sentences_removed),
            fix_code_dropped: checked.is_some_and(|c| !c.fix_code_dropped.is_empty()),
            repairs: ex.repairs,
            repaired_problems: ex.repaired_problems,
        },
        trace: take_chars(&trace.raw, TRACE_IN_ITEM_CHARS).to_string(),
    }
}

/// `results` in paste order; `primary` indexes into it. Items list the primary first.
pub fn build(results: &[Explained], primary: usize, skipped_traces: usize) -> Envelope {
    let order: Vec<usize> = std::iter::once(primary)
        .chain((0..results.len()).filter(|&i| i != primary))
        .filter(|&i| i < results.len())
        .collect();
    let items: Vec<Item> = order
        .iter()
        .filter(|&&i| results[i].status == Status::Ok)
        .map(|&i| item(i + 1, &results[i]))
        .collect();
    let unexplained: Vec<usize> = order
        .iter()
        .filter(|&&i| results[i].status != Status::Ok)
        .map(|i| i + 1)
        .collect();
    let Some(top) = items.first().cloned() else {
        return Envelope {
            summary: "No error could be explained.".into(),
            items,
            likely_cause: String::new(),
            location: "unknown".into(),
            fix_suggestion: String::new(),
            how_to_verify: Vec::new(),
            confidence: "low".into(),
            needs_human: true,
            skipped_traces,
            unexplained_traces: unexplained,
        };
    };
    let first_sentence = top
        .likely_cause
        .split(". ")
        .next()
        .unwrap_or("")
        .trim_end_matches('.');
    let root_type = results
        .get(primary)
        .map_or("", |r| r.trace.root_block().exception_type.as_str());
    Envelope {
        summary: format!(
            "Root cause: {root_type} at {}. {first_sentence}.",
            top.location
        ),
        likely_cause: top.likely_cause.clone(),
        location: top.location.clone(),
        fix_suggestion: top.fix_suggestion.clone(),
        how_to_verify: top.how_to_verify.clone(),
        confidence: top.confidence.clone(),
        needs_human: top.needs_human,
        items,
        skipped_traces,
        unexplained_traces: unexplained,
    }
}

/// The data part for "no error found": the model said "not a trace". A person should look when the
/// text does contain an error word.
pub fn not_a_trace(text: &str) -> Value {
    json!({"summary": "No error found in the paste.", "items": [], "confidence": "low",
           "needs_human": has_error_text(text)})
}
