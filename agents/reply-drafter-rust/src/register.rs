//! Pure: the channel register table (subject, greeting, sign-off, bullets, length caps) and the words that
//! name a channel (email, chat and text). The table overrides the measured style where they conflict.

use std::sync::LazyLock;

use crate::util::{Regex, captures_iter, char_len, re, split_count};

pub const CHANNELS: &[&str] = &["email", "chat", "text"];

#[derive(Debug, Clone, PartialEq)]
pub struct Register {
    pub channel: &'static str,
    pub label: &'static str,
    /// A subject line is expected (email) or left out (everything else).
    pub subject: bool,
    /// The measured greeting applies.
    pub greeting: bool,
    /// A sign-off block is allowed (and expected when the examples have one).
    pub sign_off: bool,
    /// Bullet lists may be used (if the examples use them).
    pub bullets: bool,
    pub max_words: usize,
    pub max_chars: Option<usize>,
    pub max_paragraphs: Option<usize>,
    pub guidance: &'static str,
}

impl Register {
    /// The cap violation, worded for the repair call, or None.
    pub fn over_cap(&self, body: &str) -> Option<String> {
        let words = split_count(body);
        let label = self.label.to_lowercase();
        if words > self.max_words {
            return Some(format!(
                "the reply is {words} words; the cap for a {label} is {} words",
                self.max_words
            ));
        }
        let chars = char_len(body);
        match self.max_chars {
            Some(max) if chars > max => Some(format!(
                "the reply is {chars} characters; the cap for a {label} is {max} characters"
            )),
            _ => None,
        }
    }
}

static EMAIL: Register = Register {
    channel: "email",
    label: "Email",
    subject: true,
    greeting: true,
    sign_off: true,
    bullets: true,
    max_words: 200,
    max_chars: None,
    max_paragraphs: None,
    guidance: "Greeting and sign-off as measured. Short paragraphs.",
};
static CHAT: Register = Register {
    channel: "chat",
    label: "Chat message",
    subject: false,
    greeting: true,
    sign_off: false,
    bullets: true,
    max_words: 80,
    max_chars: None,
    max_paragraphs: Some(3),
    guidance: "No subject and no sign-off. At most 3 short paragraphs.",
};
static TEXT: Register = Register {
    channel: "text",
    label: "Text message",
    subject: false,
    greeting: false,
    sign_off: false,
    bullets: false,
    max_words: 50,
    max_chars: Some(320),
    max_paragraphs: Some(1),
    guidance: "No subject, no greeting line, no sign-off, no bullets. One paragraph.",
};

/// Words a user types to name where the reply goes. The last mention wins.
static CHANNEL_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)(?P<email>\b(?:e-?mails?|mail)\b)",
        r"|(?P<chat>\b(?:slack|ms teams|teams|dm|direct message|chat(?: message)?)\b)",
        r"|(?P<text>\b(?:text message|text|sms|whats ?app|imessage)\b)"
    ))
});

/// The channel the user names, by its last mention, or None.
pub fn detect_channel(text: &str) -> Option<&'static str> {
    captures_iter(&CHANNEL_RE, text)
        .last()
        .and_then(|caps| CHANNELS.iter().copied().find(|c| caps.name(c).is_some()))
}

pub fn register_for(channel: &str) -> &'static Register {
    match channel {
        "chat" => &CHAT,
        "text" => &TEXT,
        _ => &EMAIL,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn channel_words_and_caps() {
        assert_eq!(detect_channel("an email, no, a Slack DM"), Some("chat"));
        assert_eq!(detect_channel("as a text"), Some("text"));
        assert_eq!(detect_channel("hello"), None);
        assert!(register_for("text").over_cap(&"word ".repeat(51)).is_some());
        assert!(register_for("nope").subject);
    }
}
