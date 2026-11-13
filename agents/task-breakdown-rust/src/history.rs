//! Pure: the platform's chat history, read as this agent's only memory.
//!
//! On a multi-turn chat the platform prepends a budgeted slice of earlier turns to the user's text:
//!
//! ```text
//! user: <earlier user text>
//! assistant: <earlier agent reply>
//! ...
//!
//! Current message: <this message>
//! ```
//!
//! The slice may be trimmed, compressed or absent. Parsing is best-effort and never fails: a line that
//! starts `user:` or `assistant:` opens a turn, any other line continues the open turn, and text before
//! the first role line (a summary, a truncation marker) is ignored. A turn the platform compressed
//! carries its elision sigil "[⋯ " (`compressed`): its text is not the original and is never used as
//! source material. Nothing here is stored anywhere.

use std::sync::LazyLock;

use regex::Regex;

use crate::util::{last_chars, lines, re};

pub const CURRENT_MESSAGE_MARKER: &str = "\n\nCurrent message: ";
pub const USER: &str = "user";
pub const ASSISTANT: &str = "assistant";
/// The platform's elision breadcrumb (lines/items/chars elided, or a table).
pub const COMPRESSION_MARK: &str = "[⋯ ";
/// A safety cap on the history read; the platform's slice is far smaller.
pub const MAX_HISTORY_CHARS: usize = 400_000;

static ROLE_LINE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)^(user|assistant)\s*:\s?(.*)$"));

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

    pub fn compressed(&self) -> bool {
        self.text.contains(COMPRESSION_MARK)
    }
}

/// (history block, current message). No marker: no history, the whole text is the message.
pub fn split(raw: &str) -> (&str, &str) {
    match raw.rfind(CURRENT_MESSAGE_MARKER) {
        Some(idx) => (&raw[..idx], &raw[idx + CURRENT_MESSAGE_MARKER.len()..]),
        None => ("", raw),
    }
}

/// The turns in a history block, oldest first; empty or garbled history gives none.
pub fn parse(block: &str) -> Vec<Turn> {
    let mut turns = Vec::new();
    let mut role = String::new();
    let mut open: Vec<&str> = Vec::new();
    for line in lines(last_chars(block, MAX_HISTORY_CHARS)) {
        if let Some(caps) = ROLE_LINE.captures(line) {
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
