//! Pure: the platform's chat history, this agent's only memory.
//!
//! On a multi-turn chat the platform glues earlier turns in front of the message:
//!
//! ```text
//! user: <earlier user text>
//! assistant: <earlier reply>
//! ...
//!
//! Current message: <this message>
//! ```
//!
//! Each turn is `role: content`, joined by newlines, so a multi-line turn continues on the lines after
//! its `user:` / `assistant:` line. The slice is budgeted: it may be trimmed, compressed or missing, so
//! every reader here is best-effort and returns nothing rather than guessing. Text before the first
//! role line is ignored.

use std::sync::LazyLock;

use regex::Regex;

use crate::util::{lines, re};

pub const CURRENT_MESSAGE_MARKER: &str = "\n\nCurrent message: ";
pub const USER: &str = "user";
pub const ASSISTANT: &str = "assistant";

static ROLE_LINE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)^(user|assistant):[ \t]?(.*)$"));

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

/// The turns in a history block, oldest first. Garbled or empty input gives what could be read.
pub fn parse(block: &str) -> Vec<Turn> {
    let mut turns: Vec<(String, Vec<&str>)> = Vec::new();
    for line in lines(block) {
        if let Some(caps) = ROLE_LINE.captures(line) {
            let role = caps.get(1).map_or("", |m| m.as_str()).to_lowercase();
            let first = caps.get(2).map_or("", |m| m.as_str());
            turns.push((role, vec![first]));
        } else if let Some(last) = turns.last_mut() {
            last.1.push(line);
        }
    }
    turns
        .into_iter()
        .map(|(role, parts)| Turn {
            role,
            text: parts.join("\n").trim().to_string(),
        })
        .filter(|t| !t.text.is_empty())
        .collect()
}

/// The most recent reply this agent gave, or "".
pub fn last_assistant(turns: &[Turn]) -> &str {
    turns
        .iter()
        .rev()
        .find(|t| t.role == ASSISTANT)
        .map_or("", |t| t.text.as_str())
}

/// The user's earlier messages, newest first.
pub fn user_texts(turns: &[Turn]) -> Vec<&str> {
    turns
        .iter()
        .rev()
        .filter(|t| t.role == USER)
        .map(|t| t.text.as_str())
        .collect()
}

/// The user message the most recent reply answered, or "".
pub fn user_before_last_assistant(turns: &[Turn]) -> &str {
    let mut seen_reply = false;
    for turn in turns.iter().rev() {
        if turn.role == ASSISTANT {
            seen_reply = true;
        } else if seen_reply {
            return &turn.text;
        }
    }
    ""
}
