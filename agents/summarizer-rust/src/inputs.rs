//! Pure: what the user gave this turn, read from the A2A message parts.
//!
//! - The user's own words are the FIRST text part. The platform appends org policy and agent knowledge
//!   as later, unmarked text parts: those are instructions for the model, never material to summarize.
//! - In a session with prior turns the text arrives as `"{history}\n\nCurrent message: {text}"`. The text
//!   after the last marker is this turn's message; the history before it is parsed into turns
//!   (`history.rs`) and is this agent's only memory: it keeps nothing between messages.
//! - File parts (raw bytes) are decoded when they are a text type; other types are named in a note and
//!   skipped.

use std::sync::LazyLock;

use a2a::{Message, PartContent};
use regex::Regex;

use crate::history::{self, Turn};
use crate::util::{char_len, re, take_chars};

pub const MAX_SOURCE_CHARS: usize = 120_000;
pub const MIN_SOURCE_WORDS: usize = 60;
pub const INSTRUCTION_MAX_CHARS: usize = 300;
pub const SPLIT_MIN_REST_CHARS: usize = 500;

const TEXT_TYPES: &[&str] = &[
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/csv",
    "application/json",
    "text/vtt",
    "application/x-subrip",
    "text/srt",
    "application/srt",
];
const TEXT_SUFFIXES: &[(&str, &str)] = &[
    (".txt", "text/plain"),
    (".md", "text/markdown"),
    (".markdown", "text/markdown"),
    (".csv", "text/csv"),
    (".json", "application/json"),
    (".vtt", "text/vtt"),
    (".srt", "application/x-subrip"),
];
const CUE_TYPES: &[&str] = &[
    "text/vtt",
    "application/x-subrip",
    "text/srt",
    "application/srt",
];

static CUE_TIMING: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?m)^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->.*$"));
static CUE_NUMBER: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^\s*\d+\s*$"));
static VTT_HEADER: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\AWEBVTT[^\n]*\n"));
static BLANK_RUNS: LazyLock<Regex> = LazyLock::new(|| re(r"\n{3,}"));
static PARAGRAPH_BREAK: LazyLock<Regex> = LazyLock::new(|| re(r"\n\s*\n"));

/// One turn's input. `files` is decoded file text as (name, text); `notes` names skipped files.
#[derive(Debug, Clone, Default)]
pub struct Incoming {
    pub text: String,
    pub context: Vec<String>,
    pub files: Vec<(String, String)>,
    pub notes: Vec<String>,
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

/// Splits message parts into the user's text, platform context and files.
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

pub fn media_type_of(name: &str, media_type: Option<&str>) -> String {
    let declared = media_type
        .unwrap_or("")
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    if TEXT_TYPES.contains(&declared.as_str()) {
        return declared;
    }
    let by_suffix = TEXT_SUFFIXES.iter().find(|(s, _)| *s == suffix(name));
    match by_suffix {
        Some((_, kind)) => kind.to_string(),
        None if declared.is_empty() => "unknown type".to_string(),
        None => declared,
    }
}

/// (text, None) for a readable text file; (None, note) for anything else.
pub fn decode_file(
    name: &str,
    media_type: Option<&str>,
    data: Option<&[u8]>,
) -> (Option<String>, Option<String>) {
    let kind = media_type_of(name, media_type);
    let Some(data) = data.filter(|_| TEXT_TYPES.contains(&kind.as_str())) else {
        return (
            None,
            Some(format!(
                "I can't read {name} ({kind}) yet; paste its text instead."
            )),
        );
    };
    let text = String::from_utf8_lossy(data).into_owned();
    if CUE_TYPES.contains(&kind.as_str()) {
        return (Some(strip_cues(&text)), None);
    }
    (Some(text), None)
}

/// Subtitle files: drop the header, cue numbers and timings, keep the spoken lines.
pub fn strip_cues(text: &str) -> String {
    let body = text.replace("\r\n", "\n");
    let body = VTT_HEADER.replace(&body, "");
    let body = CUE_TIMING.replace_all(&body, "");
    let body = CUE_NUMBER.replace_all(&body, "");
    BLANK_RUNS.replace_all(&body, "\n\n").trim().to_string()
}

/// (instruction, source). A short first paragraph before a long rest is the instruction.
pub fn split_request(text: &str) -> (String, String) {
    let stripped = text.trim();
    if let Some(found) = PARAGRAPH_BREAK.find(stripped) {
        let head = stripped[..found.start()].trim();
        let rest = stripped[found.end()..].trim();
        if char_len(head) <= INSTRUCTION_MAX_CHARS && char_len(rest) >= SPLIT_MIN_REST_CHARS {
            return (head.to_string(), rest.to_string());
        }
    }
    (String::new(), stripped.to_string())
}

pub fn assemble_source(source: &str, files: &[(String, String)]) -> String {
    let mut pieces = Vec::new();
    if !source.trim().is_empty() {
        pieces.push(source.trim().to_string());
    }
    for (name, text) in files {
        if !text.trim().is_empty() {
            pieces.push(format!("[File: {name}]\n{}", text.trim()));
        }
    }
    pieces.join("\n\n")
}

pub fn word_count(text: &str) -> usize {
    text.split_whitespace().count()
}

/// The first `limit` chars, cut back to the last paragraph or line break when there is one.
pub fn truncate_source(source: &str, limit: usize) -> String {
    if char_len(source) <= limit {
        return source.to_string();
    }
    let head = take_chars(source, limit);
    for boundary in ["\n\n", "\n", ". "] {
        if let Some(cut) = head
            .rfind(boundary)
            .filter(|&c| char_len(&head[..c]) >= limit / 2)
        {
            return head[..cut].trim_end().to_string();
        }
    }
    head.to_string()
}
