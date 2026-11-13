//! Pure: finds the JSON in a message. Fenced ```json blocks first (one envelope each); else every
//! balanced top-level `{...}` / `[...]` in the text that parses as JSON. A JSON array of envelopes is
//! several envelopes. Text that clearly starts JSON but does not parse is reported with the parser's
//! position. What is left once the JSON is cut out is the instruction ("as slack", "compact", ...).

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

static FENCE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?s)```[ \t]*(?:json|JSON)?[ \t]*\n(.*?)\n?```").expect("valid regex")
});
static LOOKS_LIKE_JSON: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?m)(?:^|:)[ \t]*(\{[ \t\r\n]*["}]|\[[ \t\r\n]*[\[{"\d\-\]])"#)
        .expect("valid regex")
});
pub const TARGET_KEY: &str = "target";

#[derive(Debug, Clone, PartialEq)]
pub struct JsonError {
    pub message: String,
    /// A character (not byte) index into the message.
    pub position: usize,
}

impl JsonError {
    pub fn describe(&self) -> String {
        format!(
            "JSON error near character {}: {}",
            self.position, self.message
        )
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Extracted {
    pub values: Vec<Value>,
    pub instruction: String,
    /// A top-level "target" key, removed from the data ("" when absent).
    pub target: String,
    pub error: Option<JsonError>,
}

pub fn extract(text: &str) -> Extracted {
    let fenced: Vec<regex::Captures> = FENCE.captures_iter(text).collect();
    if !fenced.is_empty() {
        return from_fences(text, &fenced);
    }
    let (values, spans) = balanced_values(text);
    if !values.is_empty() {
        return finish(values, cut(text, &spans));
    }
    if let Some(looks) = LOOKS_LIKE_JSON.captures(text) {
        let start = looks.get(1).map_or(0, |m| m.start());
        return Extracted {
            instruction: text[..start].trim().to_owned(),
            error: Some(error_of(text, start)),
            ..Extracted::default()
        };
    }
    Extracted {
        instruction: text.trim().to_owned(),
        ..Extracted::default()
    }
}

fn from_fences(text: &str, fenced: &[regex::Captures]) -> Extracted {
    let spans: Vec<(usize, usize)> = fenced
        .iter()
        .filter_map(|c| c.get(0).map(|m| (m.start(), m.end())))
        .collect();
    let mut values = Vec::new();
    for found in fenced {
        let Some(body) = found.get(1) else { continue };
        if body.as_str().trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<Value>(body.as_str()) {
            Ok(value) => values.push(value),
            Err(err) => {
                let at = body.start() + byte_offset(body.as_str(), &err);
                return Extracted {
                    instruction: cut(text, &spans),
                    error: Some(JsonError {
                        message: message_of(&err),
                        position: char_index(text, at),
                    }),
                    ..Extracted::default()
                };
            }
        }
    }
    finish(values, cut(text, &spans))
}

fn finish(values: Vec<Value>, instruction: String) -> Extracted {
    let mut flat: Vec<Value> = Vec::new();
    for value in values {
        match value {
            Value::Array(items) if items.len() > 1 && items.iter().all(is_envelope) => {
                flat.extend(items)
            }
            other => flat.push(other),
        }
    }
    let mut target = String::new();
    let cleaned = flat
        .into_iter()
        .map(|value| match value {
            Value::Object(map) if map.get(TARGET_KEY).is_some_and(Value::is_string) => {
                if target.is_empty() {
                    target = map[TARGET_KEY].as_str().unwrap_or_default().to_owned();
                }
                Value::Object(map.into_iter().filter(|(k, _)| k != TARGET_KEY).collect())
            }
            other => other,
        })
        .collect();
    Extracted {
        values: cleaned,
        instruction,
        target,
        error: None,
    }
}

fn is_envelope(value: &Value) -> bool {
    value.get("summary").is_some_and(Value::is_string)
}

/// Every balanced region that parses as an object or a non-trivial array. A balanced region that
/// does not parse is skipped whole, so nothing inside a broken object is picked up on its own.
fn balanced_values(text: &str) -> (Vec<Value>, Vec<(usize, usize)>) {
    let bytes = text.as_bytes();
    let mut values = Vec::new();
    let mut spans = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] != b'{' && bytes[i] != b'[' {
            i += 1;
            continue;
        }
        let Some(end) = balanced_end(text, i) else {
            i += 1;
            continue;
        };
        if let Ok(value) = serde_json::from_str::<Value>(&text[i..end])
            && is_data(&value)
        {
            values.push(value);
            spans.push((i, end));
        }
        i = end;
    }
    (values, spans)
}

fn is_data(value: &Value) -> bool {
    match value {
        Value::Object(_) => true,
        Value::Array(items) => {
            items.len() >= 2
                || items
                    .iter()
                    .any(|v| v.is_object() || v.is_array() || v.is_string())
        }
        _ => false,
    }
}

/// Byte index just past the bracket closing the one at `start`, string-aware; None if unbalanced.
/// Brackets and quotes are ASCII, so byte positions are always character boundaries.
fn balanced_end(text: &str, start: usize) -> Option<usize> {
    let mut stack: Vec<u8> = Vec::new();
    let (mut in_string, mut escaped) = (false, false);
    for (i, &ch) in text.as_bytes().iter().enumerate().skip(start) {
        if in_string {
            if escaped {
                escaped = false;
            } else if ch == b'\\' {
                escaped = true;
            } else if ch == b'"' {
                in_string = false;
            }
            continue;
        }
        match ch {
            b'"' => in_string = true,
            b'{' => stack.push(b'}'),
            b'[' => stack.push(b']'),
            b'}' | b']' => {
                if stack.pop() != Some(ch) {
                    return None;
                }
                if stack.is_empty() {
                    return Some(i + 1);
                }
            }
            _ => {}
        }
    }
    None
}

fn error_of(text: &str, start: usize) -> JsonError {
    let candidate = &text[start..];
    let region = match balanced_end(text, start) {
        Some(end) => &text[start..end],
        None => candidate,
    };
    match serde_json::from_str::<Value>(region) {
        Err(err) => JsonError {
            message: message_of(&err),
            position: char_index(text, start + byte_offset(region, &err)),
        },
        Ok(_) => JsonError {
            message: "unexpected text after the JSON".to_owned(),
            position: char_index(text, start),
        },
    }
}

/// The byte offset in `source` that a parse error points at (serde reports a 1-based line and column).
fn byte_offset(source: &str, err: &serde_json::Error) -> usize {
    let line_start: usize = source
        .split_inclusive('\n')
        .take(err.line().saturating_sub(1))
        .map(str::len)
        .sum();
    let mut at = (line_start + err.column().saturating_sub(1)).min(source.len());
    while !source.is_char_boundary(at) {
        at -= 1;
    }
    at
}

fn char_index(text: &str, byte: usize) -> usize {
    text.get(..byte).map_or(0, |head| head.chars().count())
}

/// The parser's complaint in plain words, without serde's "at line L column C" suffix.
fn message_of(err: &serde_json::Error) -> String {
    let full = err.to_string();
    let bare = full
        .rsplit_once(" at line ")
        .map_or(full.as_str(), |(head, _)| head);
    match bare {
        "expected `,` or `}`" | "expected `,` or `]`" => "Expecting ',' delimiter".to_owned(),
        "expected `:`" => "Expecting ':' delimiter".to_owned(),
        "key must be a string" => "Expecting property name enclosed in double quotes".to_owned(),
        "trailing comma" => "Expecting a value, found a trailing comma".to_owned(),
        "expected value" => "Expecting value".to_owned(),
        "trailing characters" => "Extra data".to_owned(),
        other => other.to_owned(),
    }
}

/// `text` with the JSON spans removed and the whitespace collapsed.
fn cut(text: &str, spans: &[(usize, usize)]) -> String {
    let mut pieces: Vec<&str> = Vec::new();
    let mut last = 0;
    for &(start, end) in spans {
        pieces.push(&text[last..start]);
        last = end;
    }
    pieces.push(&text[last..]);
    pieces
        .join(" ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn fenced_block_is_the_json_and_the_rest_is_the_instruction() {
        let found =
            extract("Format this as Slack:\n```json\n{\"summary\": \"s\", \"a\": 1}\n```\nthanks");
        assert_eq!(found.values, vec![json!({"summary": "s", "a": 1})]);
        assert_eq!(found.instruction, "Format this as Slack: thanks");
    }

    #[test]
    fn first_balanced_object_in_prose_with_strings_holding_brackets() {
        let found = extract(
            r#"as email please {"summary": "a } tricky ] one", "x": [1, {"y": "{"}]} done"#,
        );
        assert_eq!(
            found.values,
            vec![json!({"summary": "a } tricky ] one", "x": [1, {"y": "{"}]})]
        );
        assert_eq!(found.instruction, "as email please done");
    }

    #[test]
    fn array_value_and_array_of_envelopes() {
        assert_eq!(extract("[1, 2, 3]").values, vec![json!([1, 2, 3])]);
        let two = extract(r#"[{"summary": "one"}, {"summary": "two"}]"#);
        assert_eq!(
            two.values,
            vec![json!({"summary": "one"}), json!({"summary": "two"})]
        );
    }

    #[test]
    fn two_fenced_envelopes_are_two_values() {
        let text = "```json\n{\"summary\": \"one\"}\n```\n\n```json\n{\"summary\": \"two\"}\n```";
        assert_eq!(extract(text).values.len(), 2);
    }

    #[test]
    fn target_key_is_read_and_removed() {
        let found = extract(r#"{"target": "slack", "summary": "s"}"#);
        assert_eq!(found.target, "slack");
        assert_eq!(found.values, vec![json!({"summary": "s"})]);
    }

    #[test]
    fn broken_json_reports_the_parser_position() {
        for text in [
            "as slack:\n{\"summary\": \"s\", \"items\": [1, 2,]}",
            r#"{"a": 1,,}"#,
            r#"[{"a": 1]"#,
        ] {
            let found = extract(text);
            assert!(found.values.is_empty(), "{text}");
            let error = found.error.expect("an error");
            assert!(
                error.describe().starts_with("JSON error near character "),
                "{text}"
            );
        }
    }

    #[test]
    fn prose_has_no_json_and_no_error() {
        for text in [
            "what's 2+2?",
            "write a poem",
            "see [1] and {x}",
            "[link](https://x.io)",
        ] {
            let found = extract(text);
            assert!(found.values.is_empty() && found.error.is_none(), "{text}");
        }
    }

    #[test]
    fn error_position_points_into_the_message() {
        let text = r#"as slack: {"a": 1 "b": 2}"#;
        let error = extract(text).error.expect("an error");
        assert_eq!(text.chars().nth(error.position), Some('"'));
        assert!(error.message.contains("delimiter"), "{}", error.message);
    }

    #[test]
    fn error_position_counts_characters() {
        let text = "é é: {\"a\": 1 \"b\": 2}";
        let error = extract(text).error.expect("an error");
        assert_eq!(text.chars().nth(error.position), Some('"'));
    }
}
