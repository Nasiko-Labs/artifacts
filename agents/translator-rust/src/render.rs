//! Pure: the reply text. By default the translation, then one short footer line; "as json" (or metadata
//! `{"output": "json"}`) gives the fenced envelope; an envelope from another agent comes back as that
//! envelope with its fields translated. Nothing here adds HTML or images.

use serde_json::{Map, Value};

use crate::languages;

pub const HINT: &str = "Say \"more formal\", \"now in French\", \"show the original\".";
pub const REVIEW: &str = "A person should review this before it is used.";
pub const MAX_FOOTER_NOTES: usize = 4;
/// A phrase: one note at most, so the translation leads and is not drowned.
pub const SHORT_TRANSLATION_CHARS: usize = 80;

pub fn fenced_json(data: &Map<String, Value>) -> String {
    let body = serde_json::to_string_pretty(data).unwrap_or_else(|_| "{}".into());
    format!("```json\n{body}\n```")
}

/// The envelope with the translated input envelope added, when there is one.
pub fn data(
    envelope: &Map<String, Value>,
    translated_input: Option<&Map<String, Value>>,
) -> Map<String, Value> {
    let mut out = envelope.clone();
    if let Some(input) = translated_input {
        out.insert("translated_envelope".into(), Value::Object(input.clone()));
    }
    out
}

pub fn reply(
    envelope: &Map<String, Value>,
    translated_input: Option<&Map<String, Value>>,
    json_mode: bool,
) -> String {
    if json_mode {
        return fenced_json(&data(envelope, translated_input));
    }
    let body = match translated_input {
        Some(input) => fenced_json(input),
        None => text(envelope, "translation"),
    };
    format!("{body}\n\n{}", footer(envelope))
}

fn text(envelope: &Map<String, Value>, key: &str) -> String {
    envelope
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

pub fn footer(envelope: &Map<String, Value>) -> String {
    let target = languages::name_of(&text(envelope, "target_language"));
    let head = format!(
        "{target} ({}) from {}.",
        text(envelope, "register"),
        languages::name_for(&text(envelope, "source_language"))
    );
    let short = crate::util::char_len(&text(envelope, "translation")) <= SHORT_TRANSLATION_CHARS;
    let cap = if short { 1 } else { MAX_FOOTER_NOTES };
    let notes: Vec<String> = envelope
        .get("notes")
        .and_then(Value::as_array)
        .map(|n| n.iter().take(cap).map(note).collect())
        .unwrap_or_default();
    let mut pieces = vec![head];
    if !notes.is_empty() {
        pieces.push(format!("Notes: {}", notes.join(" ")));
    }
    let confidence = text(envelope, "confidence");
    if confidence != "high" {
        pieces.push(format!("Confidence: {confidence}."));
    }
    if envelope
        .get("needs_human")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        pieces.push(REVIEW.to_string());
    }
    pieces.push(HINT.to_string());
    format!("_{}_", pieces.join(" ").replace('_', "\\_"))
}

fn note(note: &Value) -> String {
    let field = |k: &str| {
        note.get(k)
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string()
    };
    let body = format!("{}.", field("note").trim_end_matches('.'));
    let term = field("term");
    if term.is_empty() {
        body
    } else {
        format!("\"{term}\": {body}")
    }
}

pub fn original(source: &str, envelope: Option<&Map<String, Value>>) -> String {
    match envelope {
        Some(input) => fenced_json(input),
        None => source.to_string(),
    }
}
