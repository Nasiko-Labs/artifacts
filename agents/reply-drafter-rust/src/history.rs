//! Pure: the earlier turns the platform prepends to the user's message, parsed into (role, text).
//!
//! This agent keeps no state between messages. On a multi-turn chat the platform sends
//!
//! ```text
//! user: <earlier user text>
//! assistant: <earlier agent reply>
//! ...
//!
//! Current message: <this message>
//! ```
//!
//! as the first text part. That block is a budgeted slice of the conversation: it may be absent,
//! trimmed, compressed or garbled, so parsing is best-effort and never fails. A line that starts with
//! `user:` or `assistant:` opens a turn; every other line continues the open turn; text before the first
//! role line is dropped (it belongs to a turn whose start was trimmed away).

use std::sync::LazyLock;

use crate::util::Regex;

use crate::util::{captures, last_chars, lines, re};

pub const CURRENT_MESSAGE_MARKER: &str = "\n\nCurrent message: ";
pub const USER: &str = "user";
pub const ASSISTANT: &str = "assistant";
pub const MAX_HISTORY_CHARS: usize = 400_000;

static ROLE_LINE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)^\s*(user|assistant)\s*:\s?(.*)$"));

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Turn {
    pub role: String,
    pub text: String,
}

impl Turn {
    pub fn new(role: &str, text: &str) -> Self {
        Self {
            role: role.to_string(),
            text: text.to_string(),
        }
    }
}

/// (history block, current message). No marker: no history, the whole text is the message.
pub fn split(raw: &str) -> (&str, &str) {
    match raw.rfind(CURRENT_MESSAGE_MARKER) {
        Some(idx) => (&raw[..idx], &raw[idx + CURRENT_MESSAGE_MARKER.len()..]),
        None => ("", raw),
    }
}

/// The turns in a history block, oldest first.
pub fn parse(block: &str) -> Vec<Turn> {
    let mut turns = Vec::new();
    let mut role = String::new();
    let mut open: Vec<&str> = Vec::new();
    for line in lines(last_chars(block, MAX_HISTORY_CHARS)) {
        if let Some(caps) = captures(&ROLE_LINE, line) {
            close(&role, &open, &mut turns);
            role = caps.get(1).map_or("", |m| m.as_str()).to_lowercase();
            open = vec![caps.get(2).map_or("", |m| m.as_str())];
        } else if !role.is_empty() {
            open.push(line);
        }
    }
    close(&role, &open, &mut turns);
    turns
}

fn close(role: &str, lines: &[&str], turns: &mut Vec<Turn>) {
    let text = lines.join("\n").trim().to_string();
    if !role.is_empty() && !text.is_empty() {
        turns.push(Turn {
            role: role.to_string(),
            text,
        });
    }
}

/// The text of the most recent turn of `role` that `accept` takes, else "".
pub fn latest<'a>(turns: &'a [Turn], role: &str, accept: impl Fn(&str) -> bool) -> &'a str {
    turns
        .iter()
        .rev()
        .find(|t| t.role == role && accept(&t.text))
        .map_or("", |t| t.text.as_str())
}

/// Every `role` turn's text, most recent first.
pub fn texts<'a>(turns: &'a [Turn], role: &str) -> Vec<&'a str> {
    turns
        .iter()
        .rev()
        .filter(|t| t.role == role)
        .map(|t| t.text.as_str())
        .collect()
}
