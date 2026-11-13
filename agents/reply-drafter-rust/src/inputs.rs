//! Pure: reading one user message into the incoming message, the user's instruction (purpose and extras)
//! and their past replies (examples). Sections start at a cue ("reply to this:", "my past replies:"); the
//! instruction is the text before the first cue plus a trailing instruction paragraph or clause.

use std::sync::LazyLock;

use crate::history::{self, Turn};
use crate::incoming::{Incoming, envelope_text, has_headers, header_start, read_incoming};
use crate::register::detect_channel;
use crate::util::{
    Regex, after_char, captures, char_len, find, find_iter, find_iter_from, group, is_match, nth,
    re, replace_all, split, strip_chars, take_chars, thousands,
};

pub const MAX_EXAMPLES: usize = 3;
pub const MAX_EXAMPLE_CHARS: usize = 1_500;
pub const MAX_TAIL_CHARS: usize = 300;

const KIND: &str = r"(?:e-?mail|mail|slack|teams|chat|dm|text|sms|whats ?app)";
const INSTR: &str = concat!(
    r"(?:say|decline|accept|agree|offer|propose|suggest|refuse|reject|turn (?:it|them|this|that) down",
    r"|push back|apologi[sz]e|thank (?:them|her|him)|tell (?:them|her|him)|ask (?:them|her|him|for|if|whether)",
    r"|let (?:them|her|him) know|i want to|i'd like to|i need to|purpose\s*:)"
);

static INCOMING_CUE: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        concat!(
            r"(?i)\b(?:(?:reply|respond|answer|write back) to(?: (?:this|the following|that|her|his|their))?(?: {kind})?",
            r"(?: (?:message|msg|email|text|note|dm))?(?: from (?P<who>[A-Za-z][\w'’-]*(?: [A-Z][\w'’-]*)?))?",
            r"|(?:reply|respond|write back) to (?P<to>(?!(?:this|that|them|her|him|it)\b)[A-Za-z][\w'’-]*)",
            r"|incoming(?: message)?|(?:they|she|he) (?:wrote|said|sent)|here(?:'s| is) (?:the|their|her|his) ",
            r"(?:email|message))\s*:"
        ),
        kind = KIND
    ))
});
static EXAMPLES_CUE: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?im)(?:\bmy (?:past|previous|old|earlier|recent|usual) (?:replies|reply|messages|emails)|\bpast replies",
        r"|\bhere(?:'s| is| are) how i (?:reply|write|respond)|\bhow i (?:usually )?(?:reply|write)",
        r"|\bmy replies|\bexamples?(?: of (?:my|how i) (?:replies|reply|write|writing))?)\s*:",
        r"|^\s*example\s*1\s*[:.)\-]"
    ))
});
static INSTR_PARAGRAPH: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)^(?:(?:please|politely|just|and)\s+)?{INSTR}"
    ))
});
static INSTR_INLINE: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)(?:[,;.!?]|\s[-–—])\s+(?=(?:(?:politely|and)\s+)?{INSTR}\b)"
    ))
});
static REPLY_WORD: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\b(?:reply|respond|response|answer)\b"));
static PARAGRAPH_BREAK: LazyLock<Regex> = LazyLock::new(|| re(r"\n\s*\n"));
static EXAMPLE_SPLIT: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?im)^\s*(?:-{3,}|={3,}|example\s*\d+\s*[:.)\-]?)\s*$|^\s*example\s*\d+\s*[:.)\-]\s*")
});
static LETTERS: LazyLock<Regex> = LazyLock::new(|| re(r"[A-Za-z]{2,}"));
static SIGN_OFF_AS: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)\bsign(?:ed)?[ -]?off (?:as|with)\s+([^\n,;]{1,30}?)\s*(?:[.;,]\s|[.;,]?$|\n)")
});
static SHORT: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\b(?:keep it short|short|brief)\b"));
static SUBJECT_WANTED: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\binclude a subject\b|\bwith a subject\b"));
static JSON: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\bas json\b|\bjson only\b|\bin json\b"));
static WORDS: LazyLock<Regex> = LazyLock::new(|| re(r"[a-z']+"));
const FILLER: &[&str] = &[
    "reply",
    "replies",
    "respond",
    "response",
    "answer",
    "draft",
    "write",
    "back",
    "to",
    "this",
    "that",
    "the",
    "following",
    "a",
    "an",
    "me",
    "my",
    "please",
    "for",
    "email",
    "e-mail",
    "mail",
    "message",
    "slack",
    "teams",
    "chat",
    "dm",
    "text",
    "sms",
    "whatsapp",
    "here",
    "is",
    "it",
    "them",
    "can",
    "you",
    "could",
    "short",
    "brief",
    "as",
    "json",
    "in",
    "only",
    "and",
    "help",
    "with",
    "i",
    "need",
    "of",
    "past",
    "previous",
    "examples",
    "example",
    "how",
    "usually",
    "keep",
    "subject",
    "include",
    "from",
    "below",
    "above",
];

/// One turn as the agent receives it. This agent keeps nothing between messages: `history` (the
/// platform's "{history}\n\nCurrent message: {text}" block, parsed) is its only memory of earlier turns.
#[derive(Debug, Clone, Default)]
pub struct Request {
    /// The user's message, history prefix removed.
    pub text: String,
    /// Organization instructions (later text parts).
    pub org: Vec<String>,
    /// Message metadata `{"output": "json"}`.
    pub wants_json: bool,
    pub history: Vec<Turn>,
}

impl Request {
    /// A request from the first text part as it arrives (history block included).
    pub fn from_raw(raw: &str) -> Request {
        let (block, text) = history::split(raw);
        Request {
            text: text.to_string(),
            history: history::parse(block),
            ..Request::default()
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct Parsed {
    pub incoming: Option<Incoming>,
    /// The user's own words: purpose and extras.
    pub instruction: String,
    pub examples: Vec<String>,
    pub channel: Option<&'static str>,
    pub has_purpose: bool,
    pub short: bool,
    pub want_subject: bool,
    pub sign_off_as: String,
    pub wants_json: bool,
    pub notes: Vec<String>,
}

pub fn current_message(raw: &str) -> &str {
    history::split(raw).1
}

#[derive(Clone, Copy, PartialEq)]
enum Section {
    Head,
    Incoming,
    Examples,
}

pub fn parse(text: &str) -> Parsed {
    let text = text.trim();
    if let Some((instruction, message)) = envelope_text(text) {
        return parsed(
            Some(read_incoming(&message, "")),
            &instruction,
            Vec::new(),
            &instruction,
            Vec::new(),
        );
    }
    let inc = captures(&INCOMING_CUE, text);
    let ex = find(&EXAMPLES_CUE, text);
    let mut marks: Vec<(usize, usize, Section)> = Vec::new();
    if let Some(m) = inc.as_ref().and_then(|c| c.get(0)) {
        marks.push((m.start(), m.end(), Section::Incoming));
    }
    if let Some(m) = &ex {
        marks.push((m.start(), m.end(), Section::Examples));
    }
    marks.sort_by_key(|m| m.0);
    let mut head = marks.first().map_or(text, |m| &text[..m.0]).to_string();
    let section_text = |n: usize| {
        let end = marks.get(n + 1).map_or(text.len(), |m| m.0);
        text.get(marks[n].1..end).unwrap_or("").to_string()
    };
    let mut incoming_section = String::new();
    let mut examples_section = String::new();
    for (n, mark) in marks.iter().enumerate() {
        match mark.2 {
            Section::Incoming => incoming_section = section_text(n),
            _ => examples_section = section_text(n),
        }
    }
    let last = marks.last().map_or(Section::Head, |m| m.2);
    let source = match last {
        Section::Head => head.clone(),
        Section::Incoming => incoming_section.clone(),
        Section::Examples => examples_section.clone(),
    };
    let (body, tail) = split_tail(&source, last != Section::Examples);
    match last {
        Section::Head => head = body,
        Section::Incoming => incoming_section = body,
        Section::Examples => examples_section = body,
    }
    let (examples, notes) = split_examples(&examples_section);
    if let Some(caps) = &inc {
        let mut who = group(caps, "who");
        if who.is_empty() {
            who = group(caps, "to");
        }
        let incoming = read_incoming(&incoming_section, who);
        let cue = nth(caps, 0);
        return parsed(
            Some(incoming),
            &format!("{head}\n{tail}"),
            examples,
            &format!("{cue}\n{head}\n{tail}"),
            notes,
        );
    }
    let has_other = !tail.is_empty() || !examples.is_empty();
    let (message, before) = uncued(&head, has_other);
    let incoming = (!message.is_empty()).then(|| read_incoming(&message, ""));
    let instruction = format!("{before}\n{tail}");
    parsed(incoming, &instruction, examples, &instruction, notes)
}

/// (the incoming message, the instruction) from text with no incoming cue.
fn uncued(head: &str, other_parts: bool) -> (String, String) {
    if has_headers(head) {
        let start = header_start(head).unwrap_or(0);
        return (
            head[start..].trim().to_string(),
            head[..start].trim().to_string(),
        );
    }
    let trimmed = head.trim();
    let paragraphs: Vec<&str> = split(&PARAGRAPH_BREAK, trimmed)
        .into_iter()
        .filter(|p| !p.trim().is_empty())
        .collect();
    if paragraphs.len() >= 2 {
        let first = paragraphs[0].trim();
        if first.split_whitespace().count() <= 40
            && (is_match(&INSTR_PARAGRAPH, first) || is_match(&REPLY_WORD, first))
        {
            return (paragraphs[1..].join("\n\n"), first.to_string());
        }
        return (trimmed.to_string(), String::new());
    }
    if other_parts && !trimmed.is_empty() && !is_instruction(head) {
        return (trimmed.to_string(), String::new());
    }
    (String::new(), trimmed.to_string())
}

fn is_instruction(text: &str) -> bool {
    let stripped = text.trim();
    char_len(stripped) <= MAX_TAIL_CHARS && is_match(&INSTR_PARAGRAPH, stripped)
}

/// (the text, a trailing instruction): a last paragraph that starts with an instruction, or (inline)
/// the earliest short trailing clause that does ("<pasted message>, decline politely").
pub fn split_tail(text: &str, inline: bool) -> (String, String) {
    let stripped = text.trim();
    let paragraphs = split(&PARAGRAPH_BREAK, stripped);
    let last_raw = paragraphs.last().copied().unwrap_or("");
    let last = last_raw.trim();
    if paragraphs.len() >= 2 && char_len(last) <= MAX_TAIL_CHARS && is_match(&INSTR_PARAGRAPH, last)
    {
        let cut = stripped.len() - last_raw.len();
        return (stripped[..cut].trim().to_string(), last.to_string());
    }
    if !inline {
        return (stripped.to_string(), String::new());
    }
    let offset = stripped.len() - last_raw.len();
    for m in find_iter_from(&INSTR_INLINE, stripped, offset) {
        if char_len(&stripped[m.end()..]) <= MAX_TAIL_CHARS {
            let keep = after_char(stripped, m.start());
            return (
                stripped[..keep].trim().to_string(),
                stripped[m.end()..].trim().to_string(),
            );
        }
    }
    (stripped.to_string(), String::new())
}

pub fn split_examples(section: &str) -> (Vec<String>, Vec<String>) {
    let blocks: Vec<&str> = split(&EXAMPLE_SPLIT, section)
        .into_iter()
        .map(|b| strip_chars(b.trim(), "\"“”").trim())
        .filter(|b| is_match(&LETTERS, b))
        .collect();
    let mut notes = Vec::new();
    if blocks.len() > MAX_EXAMPLES {
        notes.push(format!("I used your first {MAX_EXAMPLES} examples."));
    }
    let kept = &blocks[..blocks.len().min(MAX_EXAMPLES)];
    if kept.iter().any(|b| char_len(b) > MAX_EXAMPLE_CHARS) {
        notes.push(format!(
            "I read the first {} characters of each long example.",
            thousands(MAX_EXAMPLE_CHARS)
        ));
    }
    let examples = kept
        .iter()
        .map(|b| take_chars(b, MAX_EXAMPLE_CHARS).to_string())
        .collect();
    (examples, notes)
}

fn parsed(
    incoming: Option<Incoming>,
    instruction: &str,
    examples: Vec<String>,
    channel_text: &str,
    mut notes: Vec<String>,
) -> Parsed {
    let instruction = instruction.trim().to_string();
    let sign_off_as = captures(&SIGN_OFF_AS, &instruction)
        .map(|caps| nth(&caps, 1).trim().to_string())
        .unwrap_or_default();
    let is_email = incoming.as_ref().is_some_and(|i| i.is_email);
    let channel = detect_channel(channel_text).or(is_email.then_some("email"));
    if incoming.as_ref().is_some_and(|i| i.truncated) {
        notes.push(format!(
            "I read the first {} characters of the message.",
            thousands(crate::incoming::MAX_INCOMING_CHARS)
        ));
    }
    Parsed {
        incoming: incoming.filter(|i| !i.text.is_empty()),
        has_purpose: has_purpose(&instruction),
        short: is_match(&SHORT, &instruction),
        want_subject: is_match(&SUBJECT_WANTED, &instruction),
        wants_json: wants_json(&instruction),
        channel,
        sign_off_as,
        examples,
        notes,
        instruction,
    }
}

/// Whether the instruction says something beyond cue words and extras.
pub fn has_purpose(instruction: &str) -> bool {
    let no_json = replace_all(&JSON, instruction, " ");
    let rest = replace_all(&SIGN_OFF_AS, &no_json, " ").to_lowercase();
    find_iter(&WORDS, &rest)
        .iter()
        .any(|w| !FILLER.contains(&w.as_str()))
}

pub fn wants_json(text: &str) -> bool {
    is_match(&JSON, text)
}
