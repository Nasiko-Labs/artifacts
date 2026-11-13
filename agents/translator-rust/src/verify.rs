//! Pure: the checks run on each chunk the model returns, and the code fixes applied when a repair did not
//! help. Problems are worded for the repair prompt ("missing ⟦P2⟧ in segment 3", "digits changed: 14 vs
//! 41").

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use regex::{Captures, Regex};
use serde_json::{Value, json};

use crate::languages;
use crate::mask;
use crate::util::{char_len, collapse_ws, re, take_chars};

pub const LENGTH_BAND: (f64, f64) = (0.4, 2.5);
pub const CJK_LENGTH_BAND: (f64, f64) = (0.2, 2.0);
pub const LENGTH_MIN_SOURCE_CHARS: usize = 40;
pub const MAX_NOTES: usize = 6;
pub const NOTE_MAX_CHARS: usize = 240;
const TERM_MAX_CHARS: usize = 120;

static DIGITS: LazyLock<Regex> = LazyLock::new(|| re(r"[0-9]+"));

/// Arabic-Indic, Extended Arabic-Indic, Devanagari and full-width digits as ASCII.
fn ascii_digit(c: char) -> char {
    let code = c as u32;
    for zero in [0x0660, 0x06F0, 0x0966, 0xFF10] {
        if (zero..zero + 10).contains(&code) {
            return char::from_digit(code - zero, 10).unwrap_or(c);
        }
    }
    c
}

/// ASCII digit runs outside mask tokens (after mapping other scripts' digits), sorted.
pub fn digits(text: &str) -> Vec<String> {
    let mapped: String = mask::without_tokens(text)
        .chars()
        .map(ascii_digit)
        .collect();
    let mut found: Vec<String> = DIGITS
        .find_iter(&mapped)
        .map(|m| m.as_str().to_string())
        .collect();
    found.sort();
    found
}

fn counts(items: &[String]) -> HashMap<&str, usize> {
    let mut map = HashMap::new();
    for item in items {
        *map.entry(item.as_str()).or_insert(0) += 1;
    }
    map
}

pub fn token_problems(source: &str, translated: &str, n: usize) -> Vec<String> {
    let expected = mask::tokens_in(source);
    let found = mask::tokens_in(translated);
    let seen = counts(&found);
    let mut problems: Vec<String> = expected
        .iter()
        .filter(|t| !seen.contains_key(t.as_str()))
        .map(|t| format!("missing {t} in segment {n}"))
        .collect();
    problems.extend(expected.iter().filter_map(|t| {
        let times = seen.get(t.as_str()).copied().unwrap_or(0);
        (times > 1).then(|| format!("{t} appears {times} times in segment {n}; keep it once"))
    }));
    let mut foreign: Vec<&String> = Vec::new();
    for t in &found {
        if !expected.contains(t) && !foreign.contains(&t) {
            foreign.push(t);
        }
    }
    problems.extend(
        foreign
            .iter()
            .map(|t| format!("{t} does not belong in segment {n}; remove it")),
    );
    problems
}

pub fn digit_problem(source: &str, translated: &str) -> Option<String> {
    let (want, got) = (digits(source), digits(translated));
    if want == got {
        return None;
    }
    let show = |d: &[String]| {
        if d.is_empty() {
            "none".to_string()
        } else {
            d.join(" ")
        }
    };
    Some(format!("digits changed: {} vs {}", show(&want), show(&got)))
}

/// Every token and digit problem of one chunk; `first` is the 1-based number of its first segment.
pub fn chunk_problems(sources: &[String], translations: &[String], first: usize) -> Vec<String> {
    let mut found: Vec<String> = sources
        .iter()
        .zip(translations)
        .enumerate()
        .flat_map(|(offset, (src, out))| token_problems(src, out, first + offset))
        .collect();
    found.extend(digit_problem(&sources.join("\n"), &translations.join("\n")));
    found
}

pub fn unchanged(sources: &[String], translations: &[String]) -> bool {
    sources.len() == translations.len()
        && sources
            .iter()
            .zip(translations)
            .all(|(s, t)| collapse_ws(s) == collapse_ws(t))
}

/// Code fallback after a repair: duplicates and foreign tokens removed, missing tokens appended at the end
/// of the segment. Returns (text, notes).
pub fn place_tokens(
    source: &str,
    translated: &str,
    spans: &HashMap<String, String>,
    n: usize,
) -> (String, Vec<String>) {
    let expected = mask::tokens_in(source);
    let mut seen: HashSet<String> = HashSet::new();
    let mut text = mask::TOKEN
        .replace_all(translated, |c: &Captures| {
            let tok = mask::token(c.get(1).map_or(0, |m| m.as_str().parse().unwrap_or(0)));
            if !expected.contains(&tok) || seen.contains(&tok) {
                return String::new();
            }
            seen.insert(tok.clone());
            tok
        })
        .into_owned();
    let mut notes = Vec::new();
    for tok in &expected {
        if !seen.contains(tok) {
            text = format!("{} {tok}", text.trim_end());
            let original = spans.get(tok).map_or(tok.as_str(), String::as_str);
            notes.push(format!(
                "Could not place \"{original}\" in the translation; check segment {n}."
            ));
        }
    }
    (text, notes)
}

pub fn length_note(
    source: &str,
    translated: &str,
    source_code: &str,
    target_code: &str,
) -> Option<String> {
    let size = char_len(source);
    if size < LENGTH_MIN_SOURCE_CHARS {
        return None;
    }
    let cjk = languages::is_cjk(source_code) || languages::is_cjk(target_code);
    let (low, high) = if cjk { CJK_LENGTH_BAND } else { LENGTH_BAND };
    let ratio = char_len(translated) as f64 / size as f64;
    if (low..=high).contains(&ratio) {
        return None;
    }
    let word = if ratio < low { "short" } else { "long" };
    Some(format!(
        "The translation is unusually {word} for this language pair; check for omissions or additions."
    ))
}

/// Notes from the model whose `term` is found verbatim in the source; others are dropped.
pub fn model_notes(raw: &[Value], source: &str) -> Vec<Value> {
    raw.iter()
        .filter_map(|entry| {
            let entry = entry.as_object()?;
            let text = |k: &str| entry.get(k).and_then(Value::as_str).unwrap_or("").trim().to_string();
            let (term, note) = (text("term"), text("note"));
            if term.is_empty() || note.is_empty() || !source.contains(&term) {
                return None;
            }
            Some(json!({"term": take_chars(&term, TERM_MAX_CHARS), "note": take_chars(&note, NOTE_MAX_CHARS)}))
        })
        .take(MAX_NOTES)
        .collect()
}
