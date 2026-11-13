//! Pure: the fidelity check. Every scalar string and number in the input (except the footer keys) must
//! appear in the rendered output once the target's escaping is undone. Values a renderer left out on
//! purpose (cut cells, collapsed Slack blocks) are excluded and counted. Markdown fields are converted, not
//! copied, so for them every word must appear, in order.
//!
//! A failure is a bug in a renderer: the caller falls back to Markdown, never to partial output.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use crate::md_blocks;
use crate::pyjson;
use crate::shapes::{FOOTER_KEYS, is_markdown_key};

static WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^\W_]+").expect("valid regex"));

#[derive(Debug, Clone, PartialEq)]
pub struct Outcome {
    pub missing: Vec<String>,
    pub excluded: usize,
}

impl Outcome {
    pub fn ok(&self) -> bool {
        self.missing.is_empty()
    }
}

/// (plain values, markdown values) that must survive rendering.
pub fn expected(values: &[Value]) -> (Vec<String>, Vec<String>) {
    let mut plain = Vec::new();
    let mut markdown = Vec::new();
    for value in values {
        let Value::Object(map) = value else {
            walk(value, &mut plain);
            continue;
        };
        for (key, item) in map {
            if FOOTER_KEYS.contains(&key.as_str()) && !item.is_object() && !item.is_array() {
                continue;
            }
            match item {
                Value::String(s) if is_markdown_key(key) => markdown.push(s.clone()),
                other => walk(other, &mut plain),
            }
        }
    }
    (plain, markdown)
}

fn walk(value: &Value, out: &mut Vec<String>) {
    match value {
        Value::Object(map) => map.values().for_each(|v| walk(v, out)),
        Value::Array(items) => items.iter().for_each(|v| walk(v, out)),
        Value::String(s) => out.push(s.clone()),
        Value::Number(_) => out.push(pyjson::dumps(value)),
        Value::Bool(_) | Value::Null => {}
    }
}

fn norm(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn json_escaped(text: &str) -> String {
    let quoted = pyjson::string(text);
    quoted[1..quoted.len() - 1].to_owned()
}

fn words(text: &str) -> Vec<&str> {
    WORD.find_iter(text).map(|m| m.as_str()).collect()
}

/// `visible` is the output with the target's escaping undone.
pub fn check(values: &[Value], visible: &str, excluded: &[String]) -> Outcome {
    let (plain, markdown) = expected(values);
    let out = [norm(visible)];
    let left_out: Vec<String> = excluded.iter().map(|t| norm(t)).collect();
    let mut missing = Vec::new();
    let mut skipped = 0;
    for value in plain {
        let absent: Vec<String> = value
            .split('\n')
            .filter(|l| !l.trim().is_empty())
            .map(norm)
            .filter(|line| !found(line, &out))
            .collect();
        if absent.is_empty() {
            continue;
        }
        if absent.iter().all(|line| found(line, &left_out)) {
            skipped += 1;
        } else {
            missing.push(value);
        }
    }
    let seen = words(visible);
    missing.extend(
        markdown
            .into_iter()
            .filter(|m| !in_order(&words(&md_blocks::plain_words_source(m)), &seen)),
    );
    Outcome {
        missing,
        excluded: skipped,
    }
}

/// Every word a reader sees in `markdown` appears in `visible`, in order.
pub fn words_kept(markdown: &str, visible: &str) -> bool {
    in_order(
        &words(&md_blocks::plain_words_source(markdown)),
        &words(visible),
    )
}

fn found(line: &str, haystacks: &[String]) -> bool {
    let escaped = norm(&json_escaped(line));
    haystacks
        .iter()
        .any(|hay| hay.contains(line) || hay.contains(&escaped))
}

fn in_order(needles: &[&str], words: &[&str]) -> bool {
    let mut it = words.iter();
    needles.iter().all(|needle| it.any(|word| word == needle))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::envelope::render_values;
    use crate::fixtures;
    use crate::model::{Options, Target};
    use serde_json::json;

    #[test]
    fn every_string_value_survives_in_every_target() {
        for value in fixtures::all() {
            for target in [Target::Markdown, Target::Slack, Target::Email] {
                let result = render_values(
                    std::slice::from_ref(&value),
                    &Options {
                        target,
                        ..Options::default()
                    },
                );
                assert_eq!(result.target, target.name(), "{value} {:?}", result.notes);
                assert!(!expected(std::slice::from_ref(&value)).0.is_empty());
            }
        }
    }

    #[test]
    fn expected_skips_footer_keys_and_splits_markdown_fields() {
        let (plain, markdown) = expected(&[
            json!({"confidence": "high", "needs_human": true, "notes": "**n**", "a": "x"}),
        ]);
        assert_eq!(
            (plain, markdown),
            (vec!["x".to_owned()], vec!["**n**".to_owned()])
        );
    }

    #[test]
    fn missing_value_is_reported() {
        let result = check(
            &[json!({"a": "alpha", "b": "beta"})],
            "only alpha here",
            &[],
        );
        assert_eq!(result.missing, vec!["beta"]);
    }

    #[test]
    fn in_order_needs_the_order() {
        assert!(in_order(&["a", "c"], &["a", "b", "c"]));
        assert!(!in_order(&["c", "a"], &["a", "b", "c"]));
    }
}
