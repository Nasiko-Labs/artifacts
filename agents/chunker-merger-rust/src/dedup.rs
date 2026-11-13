//! Pure: conservative de-duplication of one merged list. Two items are duplicates only when their key text
//! is equal after normalisation and their tie fields (owner, due) are equal too; the first stays. Items that
//! share at least 90% of their words are kept and only reported as possible duplicates.

use std::collections::HashSet;

use serde_json::{Map, Value};
use unicode_normalization::UnicodeNormalization;

use crate::pyjson;

const KEY_FIELDS: [&str; 9] = [
    "quote",
    "source_quote",
    "text",
    "task",
    "what",
    "story",
    "decision",
    "translation",
    "name",
];
const TIE_FIELDS: [&str; 2] = ["owner", "due"];
const IGNORED_FIELDS: [&str; 2] = ["part", "rank"];
const NEAR_SHARE: f64 = 0.9;
/// Items beyond this are not compared for near-duplicates.
const MAX_PAIRWISE: usize = 500;

#[derive(Debug, Clone, PartialEq)]
pub struct Deduped {
    pub items: Vec<Value>,
    pub removed: usize,
    pub possible_pairs: usize,
}

/// NFKC, lower case, whitespace collapsed, ASCII punctuation and spaces trimmed from both ends.
pub fn normalise(value: &str) -> String {
    let folded: String = value.nfkc().collect::<String>().to_lowercase();
    let collapsed = folded.split_whitespace().collect::<Vec<_>>().join(" ");
    collapsed
        .trim_matches(|c: char| c.is_ascii_punctuation() || c == ' ')
        .to_owned()
}

pub fn key_text(item: &Value) -> String {
    match item {
        Value::Object(map) => {
            let named = KEY_FIELDS.iter().find_map(|name| {
                map.get(*name)
                    .and_then(Value::as_str)
                    .filter(|s| !s.trim().is_empty())
            });
            if let Some(text) = named {
                return normalise(text);
            }
            let rest: Map<String, Value> = map
                .iter()
                .filter(|(k, _)| !IGNORED_FIELDS.contains(&k.as_str()))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            pyjson::dumps_sorted(&Value::Object(rest))
        }
        Value::String(s) => normalise(s),
        other => pyjson::dumps_sorted(other),
    }
}

fn identity(item: &Value) -> (String, Vec<(&'static str, String)>) {
    let ties = match item {
        Value::Object(map) => TIE_FIELDS
            .iter()
            .filter_map(|name| {
                map.get(*name)
                    .filter(|v| !v.is_null())
                    .map(|v| (*name, normalise(&pyjson::py_str(v))))
            })
            .collect(),
        _ => Vec::new(),
    };
    (key_text(item), ties)
}

pub fn dedup(items: Vec<Value>) -> Deduped {
    let total = items.len();
    let mut seen = HashSet::new();
    let kept: Vec<Value> = items
        .into_iter()
        .filter(|item| seen.insert(identity(item)))
        .collect();
    let possible_pairs = possible_pairs(&kept);
    Deduped {
        removed: total - kept.len(),
        items: kept,
        possible_pairs,
    }
}

fn possible_pairs(items: &[Value]) -> usize {
    let keys: Vec<String> = items.iter().take(MAX_PAIRWISE).map(key_text).collect();
    let words: Vec<HashSet<&str>> = keys
        .iter()
        .map(|k| k.split_whitespace().collect())
        .collect();
    let mut pairs = 0;
    for i in 0..words.len() {
        for j in i + 1..words.len() {
            if keys[i] == keys[j] || words[i].is_empty() || words[j].is_empty() {
                continue;
            }
            let shared = words[i].intersection(&words[j]).count() as f64;
            if shared / words[i].len().max(words[j].len()) as f64 >= NEAR_SHARE {
                pairs += 1;
            }
        }
    }
    pairs
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn exact_dedup_needs_equal_tie_fields() {
        let items = vec![
            json!({"task": "Send deck", "owner": "Sam", "part": 1}),
            json!({"task": " send  DECK.", "owner": "sam", "part": 2}),
            json!({"task": "Send deck", "owner": "Lee", "part": 2}),
        ];
        let result = dedup(items);
        assert_eq!(result.removed, 1);
        assert_eq!(
            result
                .items
                .iter()
                .map(|i| i["owner"].clone())
                .collect::<Vec<_>>(),
            [json!("Sam"), json!("Lee")]
        );
    }

    #[test]
    fn normalise_folds_width_case_space_and_edge_punctuation() {
        assert_eq!(normalise("  Ｓend   the DECK!! "), "send the deck");
    }
}
