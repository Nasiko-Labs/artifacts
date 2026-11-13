//! Pure: what the user gave this turn, read from the A2A message.
//!
//! - The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
//!   instructions for the model, never meeting content.
//! - With chat history the first part is `"{history}\n\nCurrent message: {text}"`. The text after the last
//!   marker is this turn's message; the history before it is parsed into turns (`history.rs`) and is this
//!   agent's only memory: it keeps nothing between messages.
//! - File parts (inline bytes) are decoded when they are text; VTT / SRT keep their cue timings, which the
//!   transcript parser turns into turn times.
//! - Another agent's envelope (JSON with `transcript` or `text`) is accepted as the transcript.

use std::sync::LazyLock;

use a2a::{Message, PartContent};
use regex::Regex;
use serde_json::Value;

use crate::history::{self, HistoryTurn};
use crate::util::{char_len, re, take_chars};

pub const MAX_SOURCE_CHARS: usize = 120_000;
pub const MIN_SOURCE_WORDS: usize = 80;
pub const INSTRUCTION_MAX_CHARS: usize = 300;
pub const SPLIT_MIN_REST_CHARS: usize = 500;
pub const MAX_FILE_BYTES: usize = 5 * 1024 * 1024;
const ENVELOPE_FIELDS: &[&str] = &["transcript", "text"];

const TEXT_TYPES: &[&str] = &[
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/vtt",
    "application/x-subrip",
    "text/srt",
    "application/srt",
];
const TEXT_SUFFIXES: &[(&str, &str)] = &[
    (".txt", "text/plain"),
    (".md", "text/markdown"),
    (".markdown", "text/markdown"),
    (".vtt", "text/vtt"),
    (".srt", "application/x-subrip"),
];

static JSON_WORDS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\bas json\b|\bjson only\b|\bin json\b"));
static FENCED_JSON: LazyLock<Regex> = LazyLock::new(|| re(r"(?s)\A```(?:json)?\s*(.*?)\s*```\z"));
static PARAGRAPH_BREAK: LazyLock<Regex> = LazyLock::new(|| re(r"\n\s*\n"));
static BULLET: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*(?:[-*•]|\d{1,3}[.)])\s"));
static REQUEST_LINE: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)\b(?:transcript|notes|minutes|meeting|sync|call|stand-?up|process|i'?m|my name is|",
        r"as json)\b"
    ))
});
static SPEAKER_LINE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^\s*(?:[\[(]?\d{1,2}:\d{2}|[A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,3}\s*(?:[\[(]\d|:\s))")
});
static MY_NAME: LazyLock<Regex> =
    LazyLock::new(|| re(r"\b(?i:i'?m|i am|my name is)\s+([A-Z][a-z'’-]+(?: [A-Z][a-z'’-]+)?)"));
static TITLE: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)\b(?:notes|minutes|transcript)\s+(?:from|of|for)\s+(?:(?:the|our|this|my|today'?s)\s+)?",
        r"([^,.:;()\n]{3,60})"
    ))
});
static NAME_ONLY: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^\W*(?i:i'?m|i am|my name is)\s+([A-Z][a-z'’-]+(?: [A-Z][a-z'’-]+)?)\W*$")
});

/// One turn's input. `files` is decoded file text as (name, text); `notes` names skipped files.
#[derive(Debug, Clone, Default)]
pub struct Incoming {
    pub text: String,
    pub context: Vec<String>,
    pub files: Vec<(String, String)>,
    pub notes: Vec<String>,
    pub wants_json: bool,
    pub history: Vec<HistoryTurn>,
}

impl Incoming {
    pub fn text(text: &str) -> Self {
        Self {
            text: text.to_string(),
            wants_json: JSON_WORDS.is_match(text),
            ..Self::default()
        }
    }
}

/// Splits message parts into the user's text, platform context, files and the history block.
pub fn read_message(message: Option<&Message>) -> Incoming {
    let Some(message) = message else {
        return Incoming::default();
    };
    let mut texts: Vec<&str> = Vec::new();
    let mut files = Vec::new();
    let mut notes = Vec::new();
    for part in &message.parts {
        let name = part.filename.as_deref().unwrap_or("the attached file");
        let media = part.media_type.as_deref();
        let (decoded, note) = match &part.content {
            PartContent::Text(text) => {
                texts.push(text);
                continue;
            }
            PartContent::Raw(bytes) => decode_file(name, media, Some(bytes)),
            PartContent::Url(_) => decode_file(name, media, None),
            PartContent::Data(_) => continue,
        };
        if let Some(text) = decoded {
            files.push((name.to_string(), text));
        }
        notes.extend(note);
    }
    let (block, user) = texts.first().map_or(("", ""), |t| history::split(t));
    let by_metadata = message
        .metadata
        .as_ref()
        .and_then(|m| m.get("output"))
        .and_then(Value::as_str)
        .is_some_and(|v| v.to_lowercase() == "json");
    Incoming {
        text: user.to_string(),
        context: texts
            .iter()
            .skip(1)
            .filter(|t| !t.trim().is_empty())
            .map(|t| t.to_string())
            .collect(),
        files,
        notes,
        wants_json: by_metadata || JSON_WORDS.is_match(user),
        history: history::parse(block),
    }
}

fn suffix(name: &str) -> String {
    let last = name.rsplit(['/', '\\']).next().unwrap_or("");
    match last.rfind('.') {
        Some(idx) if idx > 0 && idx + 1 < last.len() => last[idx..].to_lowercase(),
        _ => String::new(),
    }
}

/// (text, None) for a readable text file; (None, note) for anything else.
pub fn decode_file(
    name: &str,
    media_type: Option<&str>,
    data: Option<&[u8]>,
) -> (Option<String>, Option<String>) {
    let declared = media_type
        .unwrap_or("")
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    let kind = if TEXT_TYPES.contains(&declared.as_str()) {
        declared
    } else {
        TEXT_SUFFIXES
            .iter()
            .find(|(s, _)| *s == suffix(name))
            .map_or(declared, |(_, k)| k.to_string())
    };
    let Some(data) = data.filter(|_| TEXT_TYPES.contains(&kind.as_str())) else {
        let shown = if kind.is_empty() {
            "unknown type"
        } else {
            &kind
        };
        return (
            None,
            Some(format!(
                "I can't read {name} ({shown}); paste its text instead."
            )),
        );
    };
    if data.len() > MAX_FILE_BYTES {
        return (
            None,
            Some(format!(
                "{name} is over 5 MB; paste the part you need instead."
            )),
        );
    }
    (Some(String::from_utf8_lossy(data).into_owned()), None)
}

/// The transcript inside another agent's envelope (`transcript`, else `text`), or None.
pub fn from_envelope(text: &str) -> Option<String> {
    let stripped = text.trim();
    let body = FENCED_JSON
        .captures(stripped)
        .and_then(|c| c.get(1))
        .map_or(stripped, |m| m.as_str());
    if !body.starts_with('{') {
        return None;
    }
    let data: Value = serde_json::from_str(body).ok()?;
    let object = data.as_object()?;
    ENVELOPE_FIELDS
        .iter()
        .filter_map(|f| object.get(*f).and_then(Value::as_str))
        .map(str::trim)
        .find(|s| !s.is_empty())
        .map(str::to_string)
}

/// (instruction, transcript). A short first paragraph before a long rest is the instruction; so is a
/// short first line that asks for the notes and is not itself a speaker line.
pub fn split_request(text: &str) -> (String, String) {
    let stripped = text.trim();
    if let Some(found) = PARAGRAPH_BREAK.find(stripped) {
        let head = stripped[..found.start()].trim();
        let rest = stripped[found.end()..].trim();
        if char_len(head) <= INSTRUCTION_MAX_CHARS
            && char_len(rest) >= SPLIT_MIN_REST_CHARS
            && !head.contains('\n')
            && !SPEAKER_LINE.is_match(head)
            && !BULLET.is_match(head)
        {
            return (head.to_string(), rest.to_string());
        }
    }
    let (first, rest) = stripped.split_once('\n').unwrap_or((stripped, ""));
    if char_len(first) <= INSTRUCTION_MAX_CHARS
        && char_len(rest) >= SPLIT_MIN_REST_CHARS
        && REQUEST_LINE.is_match(first)
        && !SPEAKER_LINE.is_match(first)
    {
        return (first.trim().to_string(), rest.trim().to_string());
    }
    (String::new(), stripped.to_string())
}

pub fn my_name_in(instruction: &str) -> String {
    MY_NAME
        .captures(instruction)
        .and_then(|c| c.get(1))
        .map_or(String::new(), |m| m.as_str().trim().to_string())
}

/// A meeting title named in the request ("Notes from the Tuesday product sync"), else "".
pub fn title_in(instruction: &str) -> String {
    let title = TITLE
        .captures(instruction)
        .and_then(|c| c.get(1))
        .map_or("", |m| m.as_str().trim());
    let mut chars = title.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().chain(chars).collect(),
        None => String::new(),
    }
}

/// The name in a message that only says who the user is ("I'm Raj"), else "".
pub fn name_only(text: &str) -> String {
    NAME_ONLY
        .captures(text)
        .and_then(|c| c.get(1))
        .map_or(String::new(), |m| m.as_str().trim().to_string())
}

pub fn word_count(text: &str) -> usize {
    text.split_whitespace().count()
}

/// The first `limit` chars, cut back to a line break when there is one in the second half.
pub fn cut(source: &str, limit: usize) -> String {
    if char_len(source) <= limit {
        return source.to_string();
    }
    let piece = take_chars(source, limit);
    match piece.rfind('\n') {
        Some(at) if char_len(&piece[..at]) >= limit / 2 => piece[..at].to_string(),
        _ => piece.to_string(),
    }
}
