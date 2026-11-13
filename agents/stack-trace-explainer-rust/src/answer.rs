//! Pure: the model's explanation, validated into a typed answer. Nothing outside the schema survives:
//! wrong shapes are rejected (and repaired once by the caller), long fields are clipped in code.

use std::sync::LazyLock;

use regex::Regex;
use serde::Serialize;
use serde_json::{Map, Value, json};

use crate::util::{lines, re};

pub const MAX_CAUSE_WORDS: usize = 70;
pub const MAX_FIX_WORDS: usize = 60;
pub const MAX_FIX_LINES: usize = 15;
pub const MAX_STEPS: usize = 4;
pub const MAX_EVIDENCE: usize = 3;

static VIEW_PREFIX: LazyLock<Regex> = LazyLock::new(|| re(r"^L[0-9]+: ?"));

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Evidence {
    pub source: String,
    pub quote: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct NeedCode {
    pub file: String,
    pub lines: String,
}

/// A validated `status: ok` explanation.
#[derive(Debug, Clone, PartialEq)]
pub struct Answer {
    pub frame_index: Value,
    pub frame_reason: Option<String>,
    pub likely_cause: Option<String>,
    pub fix_suggestion: Option<String>,
    pub fix_code: Option<String>,
    pub how_to_verify: Vec<String>,
    pub evidence: Vec<Evidence>,
    /// As the model sent it (`{"file", "lines"}`); checked against the candidate by `checks`.
    pub need_code: Option<Map<String, Value>>,
    pub confidence_notes: Option<String>,
}

impl Answer {
    /// The answer as JSON, for the repair prompt ("your previous answer").
    pub fn to_json(&self) -> Value {
        json!({
            "status": "ok", "frame_index": self.frame_index, "frame_reason": self.frame_reason,
            "likely_cause": self.likely_cause, "fix_suggestion": self.fix_suggestion,
            "fix_code": self.fix_code, "how_to_verify": self.how_to_verify, "evidence": self.evidence,
            "need_code": self.need_code, "confidence_notes": self.confidence_notes,
        })
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Validated {
    Ok(Box<Answer>),
    /// `off_topic` or `not_a_trace`.
    Sentinel(String),
}

pub fn validate(data: Option<&Value>) -> Option<Validated> {
    let object = data?.as_object()?;
    let status = object.get("status").and_then(Value::as_str)?;
    match status {
        "off_topic" | "not_a_trace" => return Some(Validated::Sentinel(status.to_string())),
        "ok" => {}
        _ => return None,
    }
    let cause = text(object.get("likely_cause"))?;
    let steps: Vec<String> = array(object.get("how_to_verify"))
        .iter()
        .map(|s| value_text(s).trim().to_string())
        .filter(|s| !s.is_empty())
        .take(MAX_STEPS)
        .collect();
    let evidence: Vec<Evidence> = array(object.get("evidence"))
        .iter()
        .filter_map(|e| {
            let quote = e.get("quote")?.as_str()?;
            let source = e.get("source").and_then(Value::as_str).unwrap_or("trace");
            Some(Evidence {
                source: source.to_string(),
                quote: quote.to_string(),
            })
        })
        .take(MAX_EVIDENCE)
        .collect();
    Some(Validated::Ok(Box::new(Answer {
        frame_index: object.get("frame_index").cloned().unwrap_or(Value::Null),
        frame_reason: text(object.get("frame_reason")),
        likely_cause: Some(clip_words(&cause, MAX_CAUSE_WORDS)),
        fix_suggestion: Some(clip_words(
            &text(object.get("fix_suggestion")).unwrap_or_default(),
            MAX_FIX_WORDS,
        )),
        fix_code: fix_code(object.get("fix_code")),
        how_to_verify: steps,
        evidence,
        need_code: object.get("need_code").and_then(Value::as_object).cloned(),
        confidence_notes: text(object.get("confidence_notes")),
    })))
}

fn array(value: Option<&Value>) -> Vec<Value> {
    value.and_then(Value::as_array).cloned().unwrap_or_default()
}

/// A JSON value as text, the way Python's `str()` would show a plain value.
fn value_text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

fn text(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

/// The model sometimes copies the view's "L27: " prefixes into its code; they are stripped. More than
/// 15 lines is dropped.
fn fix_code(value: Option<&Value>) -> Option<String> {
    let code = value
        .and_then(Value::as_str)
        .filter(|s| !s.trim().is_empty())?;
    let cleaned: Vec<String> = lines(code.trim_matches('\n'))
        .iter()
        .map(|l| VIEW_PREFIX.replace(l, "").into_owned())
        .collect();
    let cleaned = cleaned.join("\n");
    if lines(cleaned.trim()).len() > MAX_FIX_LINES {
        return None;
    }
    Some(cleaned)
}

pub fn clip_words(text: &str, max: usize) -> String {
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.len() <= max {
        return text.trim().to_string();
    }
    format!("{} …", words[..max].join(" "))
}
