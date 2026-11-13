//! Pure: reading the incoming message itself. Email headers (sender, subject), the sender's first name as
//! found on a sign-off line, the `Re:` subject, and a previous agent's envelope given as the message.

use std::sync::LazyLock;

use serde_json::Value;

use crate::util::{
    Regex, capitalize, captures, char_len, find, find_iter, lines, nth, re, replace_all, split,
    strip_chars, take_chars,
};

pub const MAX_INCOMING_CHARS: usize = 6_000;
pub const ENVELOPE_FIELDS: &[&str] = &["text", "body", "draft"];

static FROM: LazyLock<Regex> = LazyLock::new(|| re(r"(?im)^\s*from:\s*(.{1,160})$"));
static SUBJECT: LazyLock<Regex> = LazyLock::new(|| re(r"(?im)^\s*subject:\s*(.{1,200})$"));
static HEADER: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?im)^\s*(?:from|to|cc|subject|date|sent):"));
static RE_PREFIX: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)^(?:(?:re|fwd?|aw)\s*:\s*)+"));
static CLOSER_NAME: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)^(?:thanks|thank you|many thanks|best|best regards|kind regards|regards|cheers|",
        r"sincerely|warmly|talk soon|take care|thx)[,!.]?\s*[-–]?\s*([A-Z][a-z'’-]+)\b.{0,20}$"
    ))
});
static NAME_LINE: LazyLock<Regex> =
    LazyLock::new(|| re(r"^[-–—~]?\s*([A-Z][a-z'’-]+)(?:\s+[A-Z][a-z'’-]+)?\.?$"));
static JSON_START: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^\s*(?:```(?:json)?\s*)?\{"));
static FENCE_END: LazyLock<Regex> = LazyLock::new(|| re(r"\s*```\s*$"));
static FENCE_START: LazyLock<Regex> = LazyLock::new(|| re(r"^```(?:json)?\s*"));
static ANGLES: LazyLock<Regex> = LazyLock::new(|| re(r"[<>]"));
static SENTENCE_GAP: LazyLock<Regex> = LazyLock::new(|| re(r"(?<=[.!?])\s+"));

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Incoming {
    pub text: String,
    /// The sender's first name, when the message shows it.
    pub sender: String,
    /// The incoming subject line, without any Re:/Fwd: prefix.
    pub subject: String,
    /// It carries email headers.
    pub is_email: bool,
    pub truncated: bool,
}

impl Incoming {
    pub fn reply_subject(&self) -> String {
        if self.subject.is_empty() {
            String::new()
        } else {
            format!("Re: {}", self.subject)
        }
    }
}

pub fn read_incoming(text: &str, cue_sender: &str) -> Incoming {
    let raw = text.trim();
    let capped = take_chars(raw, MAX_INCOMING_CHARS);
    let subject = captures(&SUBJECT, capped)
        .map(|caps| replace_all(&RE_PREFIX, nth(&caps, 1).trim(), "").into_owned())
        .unwrap_or_default();
    let mut sender = from_header(capped);
    if sender.is_empty() {
        sender = first_name(cue_sender);
    }
    if sender.is_empty() {
        sender = from_sign_off(capped);
    }
    Incoming {
        text: capped.to_string(),
        sender,
        is_email: find(&FROM, capped).is_some() || find(&SUBJECT, capped).is_some(),
        subject,
        truncated: char_len(raw) > MAX_INCOMING_CHARS,
    }
}

pub fn has_headers(text: &str) -> bool {
    find(&FROM, text).is_some()
}

/// Where the first email header line starts (for text pasted without a cue).
pub fn header_start(text: &str) -> Option<usize> {
    find(&HEADER, text).map(|m| m.start())
}

fn from_header(text: &str) -> String {
    let Some(caps) = captures(&FROM, text) else {
        return String::new();
    };
    let value = strip_chars(nth(&caps, 1).trim(), "\"");
    let mut name = strip_chars(value.split('<').next().unwrap_or("").trim(), "\"").to_string();
    if name.is_empty() && value.contains('@') {
        let bare = replace_all(&ANGLES, value, "");
        let local = bare.split('@').next().unwrap_or("");
        name = capitalize(local.split('.').next().unwrap_or(""));
    }
    first_name(&name)
}

pub fn first_name(name: &str) -> String {
    let mut first = name.split_whitespace().next().unwrap_or("").to_string();
    if let Some((_, rest)) = name.split_once(',') {
        // "Lee, Dana" -> Dana
        if let Some(word) = rest.split_whitespace().next() {
            first = word.to_string();
        }
    }
    let first = strip_chars(&first, ".,;:'\"");
    let letters = first.replace(['-', '\''], "");
    let ok = first.chars().next().is_some_and(char::is_uppercase)
        && !letters.is_empty()
        && letters.chars().all(char::is_alphabetic);
    if ok { first.to_string() } else { String::new() }
}

fn from_sign_off(text: &str) -> String {
    let trimmed = text.trim();
    let mut all: Vec<&str> = lines(trimmed)
        .into_iter()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    if all.len() < 2 {
        let tail = split(&SENTENCE_GAP, trimmed);
        all = if tail.len() >= 2 { tail } else { Vec::new() };
    }
    let start = all.len().saturating_sub(2);
    let last_index = all.len().saturating_sub(1);
    for (index, line) in all.iter().enumerate().skip(start).rev() {
        if let Some(caps) = captures(&CLOSER_NAME, line) {
            return first_name(nth(&caps, 1));
        }
        if index == last_index
            && let Some(caps) = captures(&NAME_LINE, line)
        {
            return first_name(nth(&caps, 1));
        }
    }
    String::new()
}

/// (the text before it, the message) when the message is given as a previous agent's JSON envelope
/// with a `text`, `body` or `draft` field; else None.
pub fn envelope_text(text: &str) -> Option<(String, String)> {
    for m in find_iter(&JSON_START, text) {
        let rest = text[m.start()..].trim();
        let without_end = replace_all(&FENCE_END, rest, "");
        let blob = replace_all(&FENCE_START, &without_end, "").into_owned();
        let Ok(Value::Object(data)) = serde_json::from_str::<Value>(&blob) else {
            continue;
        };
        for key in ENVELOPE_FIELDS {
            if let Some(value) = data.get(*key).and_then(Value::as_str)
                && !value.trim().is_empty()
            {
                return Some((
                    text[..m.start()].trim().to_string(),
                    value.trim().to_string(),
                ));
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn senders_from_headers_cues_and_sign_offs() {
        let email = read_incoming(
            "From: Lee, Dana <d@x.example>\nSubject: Re: RE: Budget\n\nok?",
            "",
        );
        assert_eq!(
            (email.sender.as_str(), email.reply_subject().as_str()),
            ("Dana", "Re: Budget")
        );
        assert!(email.is_email);
        assert_eq!(
            read_incoming("From: <priya.shah@x.example>\n\nhi", "").sender,
            "Priya"
        );
        assert_eq!(read_incoming("can you cover Sunday?", "Raj").sender, "Raj");
        assert_eq!(
            read_incoming("Can we meet?\n\nThanks,\nPriya", "").sender,
            "Priya"
        );
        assert_eq!(read_incoming("Can we meet? Cheers, Tom", "").sender, "Tom");
        assert_eq!(read_incoming("hello there", "").sender, "");
    }
}
