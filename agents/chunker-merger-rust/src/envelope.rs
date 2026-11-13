//! Pure: reading the JSON results a user pastes for a merge. Accepts fenced json blocks, a JSON array of
//! results, or objects one after another (separated by --- or nothing). Anything that does not parse is
//! named by position, never guessed at.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use crate::pyjson;
use crate::replies::thousands;

static FENCE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?is)```[ \t]*(?:json)?[ \t]*\n(.*?)```").expect("valid regex"));
static JSON_START: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\[{]").expect("valid regex"));
static NEXT_LINE_START: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\n[ \t]*[\[{]").expect("valid regex"));

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Pasted {
    pub values: Vec<Value>,
    pub problems: Vec<String>,
}

pub fn extract_values(text: &str) -> Pasted {
    let blocks: Vec<&str> = FENCE
        .captures_iter(text)
        .filter_map(|c| c.get(1))
        .map(|m| m.as_str())
        .collect();
    if blocks.is_empty() {
        return scan(text);
    }
    let mut pasted = Pasted::default();
    for (n, block) in blocks.iter().enumerate() {
        match serde_json::from_str::<Value>(block) {
            Ok(value) => pasted.values.extend(flatten(value)),
            Err(e) => pasted.problems.push(format!(
                "Block {} isn't valid JSON ({} at line {}); ignored.",
                n + 1,
                message_of(&e),
                e.line()
            )),
        }
    }
    pasted
}

fn scan(text: &str) -> Pasted {
    let mut pasted = Pasted::default();
    let mut pos = 0;
    while let Some(found) = JSON_START.find_at(text, pos) {
        let start = found.start();
        let mut stream = serde_json::Deserializer::from_str(&text[start..]).into_iter::<Value>();
        match stream.next() {
            Some(Ok(value)) => {
                pasted.values.extend(flatten(value));
                pos = start + stream.byte_offset();
            }
            Some(Err(e)) => {
                let at = text[..start].chars().count() + 1;
                pasted.problems.push(format!(
                    "The JSON starting at character {} doesn't parse ({}); ignored.",
                    thousands(at as u64),
                    message_of(&e)
                ));
                let Some(next) = NEXT_LINE_START.find_at(text, start + 1) else {
                    break;
                };
                pos = next.start() + 1;
            }
            None => break,
        }
    }
    pasted
}

/// A JSON array of results counts as its elements; anything else is one result.
fn flatten(value: Value) -> Vec<Value> {
    match value {
        Value::Array(items) => items,
        other => vec![other],
    }
}

pub fn fenced(value: &Value) -> String {
    format!("```json\n{}\n```", pyjson::pretty(value))
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixtures;

    #[test]
    fn values_from_fences_arrays_and_separators() {
        let (a, b) = (fixtures::ranked_1(), fixtures::ranked_2());
        let (ja, jb) = (pyjson::dumps(&a), pyjson::dumps(&b));
        let expected = vec![a.clone(), b.clone()];
        assert_eq!(
            extract_values(&format!(
                "merge these\n```json\n{ja}\n```\n```json\n{jb}\n```"
            ))
            .values,
            expected
        );
        assert_eq!(
            extract_values(&format!("merge: [{ja}, {jb}]")).values,
            expected
        );
        assert_eq!(
            extract_values(&format!("merge these: {ja}\n---\n{jb}")).values,
            expected
        );
        let broken = extract_values(&format!("```json\n{{nope\n```\n```json\n{ja}\n```"));
        assert_eq!(broken.values, vec![a]);
        assert!(broken.problems[0].starts_with("Block 1 isn't valid JSON"));
    }

    #[test]
    fn a_broken_object_is_named_by_position_and_the_next_line_is_tried() {
        let pasted = extract_values("merge: {\"a\": \n{\"b\": 1}");
        assert_eq!(pasted.values, vec![serde_json::json!({"b": 1})]);
        assert!(
            pasted.problems[0].starts_with("The JSON starting at character 8 doesn't parse"),
            "{:?}",
            pasted.problems
        );
    }
}
