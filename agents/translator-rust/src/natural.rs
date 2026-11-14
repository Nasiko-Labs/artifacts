//! Pure: a translation asked for in natural words, read in code before any LLM call, plus the code check
//! on the scope call's extraction.
//!
//! ```text
//! "how do you say thank you in German", "what's 'good morning' in French?", "translate I am hungry into
//! spanish", "i am hungry in spanish", "Spanish for where is the train station?", "what does 'gracias' mean"
//! ```
//!
//! `parse` returns (instruction, text): the instruction is only the language part ("in formal Spanish"),
//! so a language named inside the text is never read as the target. It is conservative: the language must
//! be a known one (except after an explicit "translate ... into/to"), and a "text" made only of filler
//! ("now", "it", "this") is never taken, so "now in French" stays a follow-up. `checked_extraction` keeps
//! only what the model copied from the message; anything else falls back to the whole message.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use crate::languages::{self, Match};
use crate::request;
use crate::util::{char_len, re, take_chars};

pub const NATURAL_MAX_CHARS: usize = 600;
pub const BARE_MAX_WORDS: usize = 20;
pub const TAIL_MAX_WORDS: usize = 6;
const UNKNOWN_TAIL_MAX_WORDS: usize = 3;
const TARGET_NAME_MAX_CHARS: usize = 40;
const MEANING_TARGET: &str = "into English";
const Q: &str = "\"'\u{201c}\u{201d}\u{2018}\u{2019}\u{ab}\u{bb}";

const VERB: &str = concat!(
    r"tell me how (?:to|do you|would you|i) (?:say|write)|how (?:do|would|can|should|could|will) ",
    r"(?:you|i|we|one) (?:say|write|translate)|how to (?:say|write|translate)|how is|how's|what is|",
    r"what's|whats|say|translate|tell me|give me|write|put|turn|convert|change|render"
);
static GREETING: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)^(?:hey|hi|hello|ok|okay)\b[,!.]?\s*"));
static POLITE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?")
});
static TRAIL: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)(?:[\s,]*\bplease)?[\s?!.]*$"));
static WITH_VERB: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)^(?P<verb>{VERB})\s+(?P<text>.+)\s+(?P<tail>(?:in|into|to)\s+[^\n]{{1,60}})$"
    ))
});
static BARE: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)^(?P<text>.+)\s+(?P<tail>(?:in|into)\s+[^\n]{1,60})$"));
static LANG_FOR: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)^(?P<what>(?:what is|what's|whats)\s+)?(?:the\s+)?(?P<lang>[^\n]{1,40}?)\s+",
        r"(?:(?:word|phrase|term|expression)\s+)?for\s+(?P<text>.+)$"
    ))
});
static MEANING: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)^what does\s+(?P<text>.+?)\s+mean(?:\s+(?P<tail>in\s+[^\n]{1,60}))?$")
});
static WORD_FOR: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)^(?:the\s+)?(?:word|phrase|term|expression)\s+for\s+"));
static WORD: LazyLock<Regex> = LazyLock::new(|| re(r"[^\W_]+(?:'[^\W_]+)?"));
static QUOTES: LazyLock<Regex> = LazyLock::new(|| re(&format!("[{Q}]")));

const SETTING_WORDS: &[&str] = &[
    "in",
    "into",
    "to",
    "please",
    "a",
    "the",
    "language",
    "and",
    "but",
    "with",
    "using",
    "formal",
    "formally",
    "casual",
    "casually",
    "informal",
    "informally",
    "polite",
    "politely",
    "neutral",
    "friendly",
    "business",
    "professional",
    "relaxed",
    "colloquial",
    "tone",
    "register",
    "word",
    "phrase",
    "term",
    "expression",
];
const FILLER: &[&str] = &[
    "now",
    "again",
    "please",
    "it",
    "this",
    "that",
    "these",
    "those",
    "the",
    "a",
    "same",
    "text",
    "one",
    "too",
    "also",
    "and",
    "then",
    "me",
    "all",
    "everything",
    "above",
    "following",
    "instead",
    "more",
    "less",
    "formal",
    "casual",
    "here",
    "something",
    "anything",
    "them",
    "him",
    "her",
    "us",
    "my",
    "your",
    "message",
    "email",
];

/// What the scope call says: `Translate` (a text with an optional target name) or off topic.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Extraction {
    Translate { text: String, target: String },
    OffTopic,
}

/// (instruction, text) for a one-line natural request, or None.
pub fn parse(message: &str) -> Option<(String, String)> {
    let line = message.trim();
    if line.is_empty() || line.contains('\n') || char_len(line) > NATURAL_MAX_CHARS {
        return None;
    }
    let polite = POLITE.replace(line, "").trim().to_string();
    let greeted = POLITE
        .replace(&GREETING.replace(line, ""), "")
        .trim()
        .to_string();
    // "hey, how do you say ..." but also "hello in Spanish"
    let mut cores = vec![greeted];
    if cores[0] != polite {
        cores.push(polite);
    }
    cores.iter().find_map(|core| {
        with_verb(core)
            .or_else(|| meaning(core))
            .or_else(|| lang_for(core))
            .or_else(|| bare(core))
    })
}

fn with_verb(core: &str) -> Option<(String, String)> {
    let trimmed = trimmed(core);
    let found = WITH_VERB.captures(&trimmed)?;
    let text = WORD_FOR.replace(&found["text"], "").to_string();
    let allow_unknown = found["verb"].eq_ignore_ascii_case("translate");
    result(&found["tail"], &text, allow_unknown)
}

fn meaning(core: &str) -> Option<(String, String)> {
    let trimmed = trimmed(core);
    let found = MEANING.captures(&trimmed)?;
    let tail = found.name("tail").map_or(MEANING_TARGET, |m| m.as_str());
    result(tail, &found["text"], false)
}

fn lang_for(core: &str) -> Option<(String, String)> {
    let found = LANG_FOR.captures(core)?;
    let mut text = found["text"].to_string();
    if found.name("what").is_some() {
        // "what's the Spanish word for dog?": the "?" closes the question to me
        text = trimmed(&text);
    }
    result(&format!("in {}", &found["lang"]), &text, false)
}

fn bare(core: &str) -> Option<(String, String)> {
    let trimmed = trimmed(core);
    if trimmed.split_whitespace().count() > BARE_MAX_WORDS {
        return None;
    }
    let found = BARE.captures(&trimmed)?;
    result(&found["tail"], &found["text"], false)
}

fn result(tail: &str, text: &str, allow_unknown: bool) -> Option<(String, String)> {
    let instruction = tail.trim_matches(|c: char| c == ' ' || c == ',');
    let source = unquoted(text);
    if source.is_empty() || only_filler(&source) || !is_language_tail(instruction, allow_unknown) {
        return None;
    }
    Some((instruction.to_string(), source))
}

fn words(text: &str) -> Vec<String> {
    let lower = text.to_lowercase();
    WORD.find_iter(&lower)
        .map(|m| m.as_str().to_string())
        .collect()
}

fn is_language_tail(tail: &str, allow_unknown: bool) -> bool {
    let all = words(tail);
    if all.is_empty() || all.len() > TAIL_MAX_WORDS {
        return false;
    }
    if languages::find(tail).0.is_none() {
        return allow_unknown
            && !request::unknown_language(tail).is_empty()
            && all.len() <= UNKNOWN_TAIL_MAX_WORDS;
    }
    words(&languages::strip_names(tail))
        .iter()
        .all(|w| SETTING_WORDS.contains(&w.as_str()))
}

fn only_filler(text: &str) -> bool {
    words(text).iter().all(|w| FILLER.contains(&w.as_str()))
}

fn trimmed(text: &str) -> String {
    TRAIL.replace(text, "").trim().to_string()
}

fn unquoted(text: &str) -> String {
    let stripped = text.trim();
    let mut chars = stripped.chars();
    match (chars.next(), chars.next_back()) {
        (Some(first), Some(last)) if Q.contains(first) && Q.contains(last) => {
            chars.as_str().trim().to_string()
        }
        _ => stripped.to_string(),
    }
}

/// The scope call's JSON; `None` when unreadable (the caller retries once, then declines).
pub fn read_extraction(parsed: Option<&Value>) -> Option<Extraction> {
    let object = parsed?.as_object()?;
    let field = |name: &str| {
        object
            .get(name)
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    };
    match object.get("status").and_then(Value::as_str)? {
        "text" => Some(Extraction::Translate {
            text: String::new(),
            target: String::new(),
        }),
        "translate" => Some(Extraction::Translate {
            text: field("text"),
            target: field("target"),
        }),
        "off_topic" => Some(Extraction::OffTopic),
        _ => None,
    }
}

/// (source, target, unknown name) from a `Translate` extraction. The text must appear in the message
/// (case, spacing and quotes ignored), else the whole message is the text. A target that is not a known
/// language is returned as `unknown`.
pub fn checked_extraction(
    text: &str,
    target: &str,
    message: &str,
) -> (String, Option<Match>, String) {
    let mut source = unquoted(text);
    if source.is_empty() || !normal(message).contains(&normal(&source)) {
        source = message.trim().to_string();
    }
    let name = take_chars(target.trim(), TARGET_NAME_MAX_CHARS).to_string();
    if name.is_empty() {
        return (source, None, String::new());
    }
    match languages::from_answer(&name) {
        Some(found) => (source, Some(found), String::new()),
        None => (source, None, name),
    }
}

fn normal(text: &str) -> String {
    QUOTES
        .replace_all(text, "")
        .to_lowercase()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}
