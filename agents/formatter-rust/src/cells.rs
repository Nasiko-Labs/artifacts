//! Pure: the shared text of a document that every renderer shows the same way (scalar spelling, the
//! footer line, the warning line). Escaping is the caller's job.

use serde_json::Value;

use crate::model::Document;
use crate::pyjson;

pub const WARNING: &str = "⚠ Needs a person to review";
pub const NONE_MARK: &str = "—";

/// Booleans as yes/no, null as an em dash, numbers unchanged, strings as given.
pub fn scalar_text(value: &Value) -> String {
    match value {
        Value::Null => NONE_MARK.to_owned(),
        Value::Bool(b) => if *b { "yes" } else { "no" }.to_owned(),
        Value::String(s) => s.clone(),
        other => pyjson::dumps(other),
    }
}

pub fn json_text(value: &Value, pretty: bool) -> String {
    if pretty {
        pyjson::pretty(value)
    } else {
        pyjson::dumps(value)
    }
}

/// ['Confidence: medium', 'Needs a person: yes'], only for the keys the input had (as raw text).
pub fn footer_parts(doc: &Document) -> Vec<String> {
    let Some((confidence, needs_human)) = &doc.footer else {
        return Vec::new();
    };
    let mut parts = Vec::new();
    if !confidence.is_null() {
        parts.push(format!("Confidence: {}", scalar_text(confidence)));
    }
    if !needs_human.is_null() {
        parts.push(format!("Needs a person: {}", scalar_text(needs_human)));
    }
    parts
}

/// The heading of one envelope among several: its summary, else its position.
pub fn doc_heading(doc: &Document, index: usize) -> String {
    match &doc.lead {
        Some(lead) if !lead.is_empty() => lead.clone(),
        _ => format!("Envelope {index}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn scalar_spelling() {
        assert_eq!(scalar_text(&json!(null)), "—");
        assert_eq!(scalar_text(&json!(true)), "yes");
        assert_eq!(scalar_text(&json!(1.25)), "1.25");
        assert_eq!(scalar_text(&json!("2026-11-07")), "2026-11-07");
    }
}
