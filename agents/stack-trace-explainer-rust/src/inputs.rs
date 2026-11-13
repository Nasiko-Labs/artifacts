//! Pure: what the user gave this turn, read from the A2A message.
//!
//! - The user's own words are the FIRST text part; with chat history it is
//!   `"{history}\n\nCurrent message: {text}"`: the text after the last marker is this turn's message,
//!   and the history before it is parsed into turns (`history.rs`), this agent's only memory.
//! - Later text parts are org policy / agent knowledge: system context for the model, never the
//!   request or evidence.
//! - A text file part (inline bytes, <= 5 MB) is treated as pasted text: a trace stays a trace,
//!   anything else becomes a code block labelled `# file: <name>`.
//! - Another step's envelope (JSON with `items[].trace` or `summary`) is read as the trace text.
//! - Pasted data is fenced for the model; the fence markers are stripped from the data first.

use std::sync::LazyLock;

use a2a::{Message, PartContent};
use regex::Regex;
use serde_json::Value;

use crate::history::{self, Turn};
use crate::util::{char_len, last_chars, re, thousands};

pub const MAX_MESSAGE_CHARS: usize = 100_000;
pub const MAX_FILE_BYTES: usize = 5 * 1024 * 1024;
const TEXT_TYPES: &[&str] = &[
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/json",
    "text/csv",
    "text/x-diff",
    "text/x-patch",
];
const TEXT_SUFFIXES: &[&str] = &[
    ".txt", ".log", ".md", ".json", ".csv", ".diff", ".patch", ".py", ".js", ".ts", ".java", ".kt",
    ".go", ".rs", ".rb", ".cs",
];

static JSON_WORDS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\bas json\b|\bjson only\b|\bin json\b"));
static MARKERS: LazyLock<Regex> = LazyLock::new(|| re(r"<<<\s*/?\s*[A-Za-z_]+\s*>>>"));
static FENCED_JSON: LazyLock<Regex> = LazyLock::new(|| re(r"(?s)\A```(?:json)?\s*(.*?)\s*```\z"));
static TRACE_START: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?m)Traceback \(most recent call last\)|^\s*at \S|panicked at|^panic: |Exception in thread",
    )
});

#[derive(Debug, Clone, Default)]
pub struct Incoming {
    pub text: String,
    pub policy: Vec<String>,
    pub wants_json: bool,
    pub notes: Vec<String>,
    /// The platform's earlier turns, oldest first: the only memory.
    pub history: Vec<Turn>,
}

impl Incoming {
    pub fn text(text: &str) -> Self {
        Self {
            text: text.to_string(),
            ..Self::default()
        }
    }
}

pub fn read_message(message: Option<&Message>) -> Incoming {
    let Some(message) = message else {
        return Incoming::default();
    };
    let mut texts: Vec<&str> = Vec::new();
    let mut files: Vec<String> = Vec::new();
    let mut notes: Vec<String> = Vec::new();
    for part in &message.parts {
        let name = part.filename.as_deref().unwrap_or("attached file");
        let media = part.media_type.as_deref();
        let decoded = match &part.content {
            PartContent::Text(text) => {
                texts.push(text);
                continue;
            }
            PartContent::Raw(bytes) => decode_file(name, media, Some(bytes)),
            PartContent::Url(_) => decode_file(name, media, None),
            PartContent::Data(_) => continue,
        };
        if let Some(text) = decoded.0 {
            files.push(text);
        }
        if let Some(note) = decoded.1 {
            notes.push(note);
        }
    }
    let (block, user) = texts.first().map_or(("", ""), |t| history::split(t));
    let wants = metadata_wants_json(message) || JSON_WORDS.is_match(user);
    let mut combined = std::iter::once(user)
        .chain(files.iter().map(String::as_str))
        .filter(|t| !t.trim().is_empty())
        .collect::<Vec<_>>()
        .join("\n\n");
    if char_len(&combined) > MAX_MESSAGE_CHARS {
        let cap = thousands(MAX_MESSAGE_CHARS);
        notes.push(format!(
            "Your message was over {cap} characters; I read the last {cap} (logs end with the fatal error)."
        ));
        combined = last_chars(&combined, MAX_MESSAGE_CHARS).to_string();
    }
    let policy = texts
        .iter()
        .skip(1)
        .filter(|t| !t.trim().is_empty())
        .map(|t| t.to_string())
        .collect();
    Incoming {
        text: combined,
        policy,
        wants_json: wants,
        notes,
        history: history::parse(block),
    }
}

fn metadata_wants_json(message: &Message) -> bool {
    message
        .metadata
        .as_ref()
        .and_then(|m| m.get("output"))
        .and_then(Value::as_str)
        .is_some_and(|v| v.to_lowercase() == "json")
}

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
    let readable =
        TEXT_TYPES.contains(&declared.as_str()) || TEXT_SUFFIXES.contains(&suffix(name).as_str());
    let Some(data) = data.filter(|_| readable) else {
        let kind = if declared.is_empty() {
            "unknown type"
        } else {
            declared.as_str()
        };
        return (
            None,
            Some(format!(
                "I can't read {name} ({kind}); paste its text instead."
            )),
        );
    };
    if data.len() > MAX_FILE_BYTES {
        return (
            None,
            Some(format!(
                "{name} is over 5 MB, so I skipped it; paste the error and the lines around it instead."
            )),
        );
    }
    let text = String::from_utf8_lossy(data).into_owned();
    if TRACE_START.is_match(&text) {
        return (Some(text), None);
    }
    (Some(format!("# file: {name}\n{text}")), None)
}

/// The lowercased suffix of the last path component (`"a/b.TXT"` -> `".txt"`), or "".
fn suffix(name: &str) -> String {
    let last = name.rsplit(['/', '\\']).next().unwrap_or("");
    match last.rfind('.') {
        Some(idx) if idx > 0 && idx + 1 < last.len() => last[idx..].to_lowercase(),
        _ => String::new(),
    }
}

pub fn strip_markers(text: &str) -> String {
    MARKERS.replace_all(text, "").into_owned()
}

/// Untrusted data between markers the data itself can no longer contain.
pub fn fence(text: &str, name: &str) -> String {
    format!("<<<{name}>>>\n{}\n<<<END>>>", strip_markers(text))
}

pub fn wants_json(text: &str) -> bool {
    JSON_WORDS.is_match(text)
}

/// The trace text inside a previous step's envelope (`items[].trace`, else `summary`), or None.
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
    let traces: Vec<&str> = object
        .get("items")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.get("trace").and_then(Value::as_str))
                .filter(|t| !t.trim().is_empty())
                .collect()
        })
        .unwrap_or_default();
    if !traces.is_empty() {
        return Some(traces.join("\n\n"));
    }
    object
        .get("summary")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}
