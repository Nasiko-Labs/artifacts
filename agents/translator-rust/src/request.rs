//! Pure: one translation job read from the user's message, before any LLM call.
//!
//! The instruction is the first line when it carries a cue ("Translate to Spanish, formal: <text>", "in
//! formal Japanese:" + text on the next lines) or the last line after the text. The text after the cue's
//! colon (or the other lines) is the source; a JSON object with translatable string fields is an envelope
//! from another agent. Target language, register and glossary are read from the instruction only, never
//! from the text.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value};

use crate::languages;
use crate::register;
use crate::util::{char_len, re, take_chars};

pub const INSTRUCTION_MAX_CHARS: usize = 200;
pub const MAX_GLOSSARY: usize = 20;
pub const MAX_SOURCE_CHARS: usize = 60_000;
pub const ENVELOPE_FIELDS: &[&str] = &[
    "draft",
    "alt_shorter",
    "text",
    "memo_markdown",
    "tldr",
    "summary",
];

const Q: &str = "\"'\u{201c}\u{201d}\u{2018}\u{2019}";
static TRANSLATE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\btranslat\w*"));
static LEADING: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)^\s*(?:now|and|please|then)?\s*(?:in|into|to)\s+\S"));
static COLON: LazyLock<Regex> = LazyLock::new(|| re(r":(?:\s|$)"));
static INLINE: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)^\s*translate\s+[{Q}](.+)[{Q}]\s+(?:in|into|to)\s+[^:]+$"
    ))
});
static KEEP: LazyLock<[Regex; 2]> = LazyLock::new(|| {
    [
        re(&format!(
            r"(?i)\b(?:keep|leave|don'?t translate|do not translate)\s+[{Q}]([^{Q}\n]{{1,60}})[{Q}]"
        )),
        re(
            r#"(?i)\b(?:keep|leave)\s+([^\s'"]{1,40})\s+(?:untranslated|as is|unchanged|in english)\b"#,
        ),
    ]
});
static AS: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)\btranslate\s+[{Q}]([^{Q}\n]{{1,60}})[{Q}]\s+as\s+[{Q}]([^{Q}\n]{{1,60}})[{Q}]"
    ))
});
static JSON_WORDS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\bas json\b|\bjson only\b|\bin json\b"));
static FENCE: LazyLock<Regex> = LazyLock::new(|| re(r"(?s)^```(?:json)?\s*(.*?)\s*```$"));
static UNKNOWN: LazyLock<Regex> =
    LazyLock::new(|| re(r"\b(?:into|to)\s+([A-Z][\w-]{2,30}|[a-z][\w-]{3,30})\b"));
const NOT_LANGUAGE: &[&str] = &[
    "this", "that", "the", "them", "it", "me", "my", "our", "your", "formal", "casual", "neutral",
    "polite", "informal", "english", "text", "email", "please", "a", "an",
];

/// What one translation needs. `source` is plain text; `envelope` replaces it for agent output.
#[derive(Debug, Clone, PartialEq)]
pub struct Job {
    pub source: String,
    pub envelope: Option<Map<String, Value>>,
    pub target: String,
    pub target_note: String,
    pub unknown: String,
    pub register: &'static str,
    pub keep: Vec<String>,
    pub glossary: Vec<(String, String)>,
    pub source_hint: String,
    pub as_json: bool,
}

impl Default for Job {
    fn default() -> Self {
        Self {
            source: String::new(),
            envelope: None,
            target: String::new(),
            target_note: String::new(),
            unknown: String::new(),
            register: register::NEUTRAL,
            keep: Vec::new(),
            glossary: Vec::new(),
            source_hint: String::new(),
            as_json: false,
        }
    }
}

impl Job {
    pub fn has_text(&self) -> bool {
        !self.source.trim().is_empty() || self.envelope.as_ref().is_some_and(|e| !e.is_empty())
    }
}

pub fn is_cue(line: &str) -> bool {
    if char_len(line) > INSTRUCTION_MAX_CHARS {
        return false;
    }
    TRANSLATE.is_match(line) || (LEADING.is_match(line) && languages::find(line).0.is_some())
}

/// (instruction, body). No cue: ("", the whole text).
pub fn split(text: &str) -> (String, String) {
    let stripped = text.trim();
    let (first, rest) = stripped.split_once('\n').unwrap_or((stripped, ""));
    if let Some(colon) = COLON.find(first) {
        // the text after the colon may be one long line
        if is_cue(&first[..colon.start()]) {
            let mut body = first[colon.end()..].to_string();
            if !rest.is_empty() {
                body.push('\n');
                body.push_str(rest);
            }
            return (
                first[..colon.start()].trim().to_string(),
                body.trim().to_string(),
            );
        }
    }
    if is_cue(first) {
        if let Some(inline) = INLINE.captures(first).filter(|_| rest.is_empty()) {
            let quoted = inline.get(1).map_or("", |m| m.as_str()).trim();
            return (first.to_string(), quoted.to_string());
        }
        return (first.trim().to_string(), rest.trim().to_string());
    }
    if let Some((head, last)) = stripped.rsplit_once('\n')
        && !head.is_empty()
        && is_cue(last)
    {
        return (
            last.trim().trim_end_matches(':').to_string(),
            head.trim().to_string(),
        );
    }
    (String::new(), stripped.to_string())
}

pub fn envelope_from(body: &str) -> Option<Map<String, Value>> {
    let trimmed = body.trim();
    let inner = FENCE
        .captures(trimmed)
        .and_then(|c| c.get(1))
        .map_or(body, |m| m.as_str());
    let data: Value = serde_json::from_str(inner).ok()?;
    let object = data.as_object()?;
    let translatable = ENVELOPE_FIELDS.iter().any(|f| {
        object
            .get(*f)
            .and_then(Value::as_str)
            .is_some_and(|s| !s.trim().is_empty())
    });
    translatable.then(|| object.clone())
}

/// (keep terms, "translate a as b" pairs) named in an instruction.
pub fn glossary(instruction: &str) -> (Vec<String>, Vec<(String, String)>) {
    let pairs: Vec<(String, String)> = AS
        .captures_iter(instruction)
        .map(|c| {
            let cell = |i| c.get(i).map_or("", |m| m.as_str()).trim().to_string();
            (cell(1), cell(2))
        })
        .take(MAX_GLOSSARY)
        .collect();
    let rest = AS.replace_all(instruction, " ");
    let mut keep: Vec<String> = Vec::new();
    for pattern in KEEP.iter() {
        for caps in pattern.captures_iter(&rest) {
            let term = caps.get(1).map_or("", |m| m.as_str()).trim().to_string();
            if !term.is_empty() && !keep.contains(&term) {
                keep.push(term);
            }
        }
    }
    keep.truncate(MAX_GLOSSARY.saturating_sub(pairs.len()));
    (keep, pairs)
}

/// The instruction's language, register, glossary and JSON wish layered over `job`.
pub fn apply_instruction(job: Job, instruction: &str) -> Job {
    let (target, source) = languages::find(instruction);
    let (keep, pairs) = glossary(instruction);
    let mut job = job;
    if let Some(target) = target {
        job.target = target.language.code.to_string();
        job.target_note = target.note.to_string();
        job.unknown.clear();
    } else {
        let unknown = unknown_language(instruction);
        if !unknown.is_empty() && job.target.is_empty() {
            job.unknown = unknown;
        }
    }
    if let Some(source) = source {
        job.source_hint = source.language.name.to_string();
    }
    if let Some(register) = register::parse(instruction) {
        job.register = register;
    }
    if !keep.is_empty() || !pairs.is_empty() {
        let mut merged = job.keep.clone();
        for term in keep {
            if !merged.contains(&term) {
                merged.push(term);
            }
        }
        merged.truncate(MAX_GLOSSARY);
        job.keep = merged;
        job.glossary.extend(pairs);
        job.glossary.truncate(MAX_GLOSSARY);
    }
    if JSON_WORDS.is_match(instruction) {
        job.as_json = true;
    }
    job
}

/// A word after "to"/"into" that is not a known language, e.g. "translate to Klingon".
pub fn unknown_language(instruction: &str) -> String {
    let Some(word) = UNKNOWN.captures(instruction).and_then(|c| c.get(1)) else {
        return String::new();
    };
    if NOT_LANGUAGE.contains(&word.as_str().to_lowercase().as_str()) {
        return String::new();
    }
    word.as_str().to_string()
}

pub fn wants_json(text: &str) -> bool {
    JSON_WORDS.is_match(text)
}

/// The first `limit` chars, cut back to the last paragraph or line break when there is one.
pub fn cut(source: &str, limit: usize) -> String {
    if char_len(source) <= limit {
        return source.to_string();
    }
    let head = take_chars(source, limit);
    for boundary in ["\n\n", "\n", ". "] {
        if let Some(at) = head
            .rfind(boundary)
            .filter(|&a| char_len(&head[..a]) >= limit / 2)
        {
            return head[..at].trim_end().to_string();
        }
    }
    head.to_string()
}
