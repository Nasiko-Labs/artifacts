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
//! the first role line (a summary, a truncation marker) is ignored. A turn the platform compressed carries
//! its elision sigil "[⋯ " (`compressed`): its text is not the original and must not be used as source
//! material. Nothing here is stored anywhere.

use std::sync::LazyLock;

use regex::Regex;

pub const CURRENT_MESSAGE_MARKER: &str = "\n\nCurrent message: ";
pub const USER: &str = "user";
/// The platform's elision breadcrumb (lines/items/chars elided, or a table).
pub const COMPRESSION_MARK: &str = "[⋯ ";

static ROLE_LINE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^(user|assistant)\s*:\s?(.*)$").expect("valid regex"));

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Turn {
    /// `user` or `assistant`.
    pub role: String,
    pub text: String,
}

impl Turn {
    #[cfg(test)]
    pub fn new(role: &str, text: &str) -> Self {
        Self {
            role: role.to_owned(),
            text: text.to_owned(),
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

/// The turns in a history block, oldest first; empty or garbled history gives no turns.
pub fn parse(block: &str) -> Vec<Turn> {
    let mut turns: Vec<(String, Vec<&str>)> = Vec::new();
    for line in block.lines() {
        if let Some(found) = ROLE_LINE.captures(line) {
            let role = found[1].to_lowercase();
            let first = found.get(2).map_or("", |m| m.as_str());
            turns.push((role, vec![first]));
        } else if let Some((_, lines)) = turns.last_mut() {
            lines.push(line);
        }
    }
    turns
        .into_iter()
        .map(|(role, lines)| Turn {
            role,
            text: lines.join("\n").trim().to_owned(),
        })
        .filter(|t| !t.text.is_empty())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_on_the_last_marker_and_keeps_multiline_turns() {
        let (block, current) =
            split("user: as slack: {\n  \"a\": 1\n}\nassistant: ok\n\nCurrent message: as email");
        assert_eq!(current, "as email");
        assert_eq!(
            parse(block),
            vec![
                Turn::new("user", "as slack: {\n  \"a\": 1\n}"),
                Turn::new("assistant", "ok")
            ]
        );
    }

    #[test]
    fn empty_or_garbled_history_parses_to_no_turns() {
        let garbled = "[summary of earlier turns]\ngarbled ::: text\n\nCurrent message: x";
        for raw in [
            "",
            "no marker",
            "\n\nCurrent message: x",
            garbled,
            "user:\nassistant: \n\nCurrent message: x",
        ] {
            assert!(parse(split(raw).0).is_empty(), "{raw:?}");
        }
    }

    #[test]
    fn role_lines_are_case_insensitive_and_compression_is_seen() {
        let turns = parse("User: a\nASSISTANT: [⋯ 3 lines elided ⋯]");
        assert_eq!(turns[0].role, "user");
        assert!(turns[1].compressed() && !turns[0].compressed());
    }
}
