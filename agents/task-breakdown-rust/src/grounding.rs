//! Pure: is a fragment really in the user's words? Used to keep assumptions, constraints and owners tied
//! to what the user typed (never to org policy or to the model's own text).

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;

use crate::util::{char_len, re, strip_chars};

pub const MIN_FRAGMENT_CHARS: usize = 3;
pub const CONTENT_TOKEN_CHARS: usize = 3;
pub const GROUNDED_SHARE: f64 = 0.6;
const STOP: &[&str] = &[
    "the", "and", "for", "with", "that", "this", "are", "our", "from", "will", "must", "should",
    "have", "has", "not", "all", "any", "but", "into", "per", "can", "use", "using",
];

static SPACE: LazyLock<Regex> = LazyLock::new(|| re(r"\s+"));
static TOKEN: LazyLock<Regex> = LazyLock::new(|| re(r"[a-z0-9]+"));

/// Curly quotes straightened, casefolded, whitespace collapsed, edge punctuation stripped.
pub fn norm(text: &str) -> String {
    let straight: String = text
        .chars()
        .map(|c| match c {
            '\u{2018}' | '\u{2019}' => '\'',
            '\u{201C}' | '\u{201D}' => '"',
            other => other,
        })
        .collect();
    let lowered = straight.to_lowercase();
    let spaced = SPACE.replace_all(&lowered, " ");
    strip_chars(spaced.trim(), ".,;:!?\"'").to_string()
}

/// The fragment, normalised, appears in the source, normalised.
pub fn is_verbatim(fragment: &str, source: &str) -> bool {
    let piece = norm(fragment);
    char_len(&piece) >= MIN_FRAGMENT_CHARS && norm(source).contains(&piece)
}

pub fn content_tokens(text: &str) -> HashSet<String> {
    let normed = norm(text);
    TOKEN
        .find_iter(&normed)
        .map(|m| m.as_str())
        .filter(|t| t.len() >= CONTENT_TOKEN_CHARS && !STOP.contains(t))
        .map(str::to_string)
        .collect()
}

/// At least 60% of the text's content words appear in the source (or the text is verbatim).
pub fn mostly_grounded(text: &str, source: &str) -> bool {
    if is_verbatim(text, source) {
        return true;
    }
    let tokens = content_tokens(text);
    if tokens.is_empty() {
        return false;
    }
    let found = content_tokens(source);
    let hits = tokens.intersection(&found).count();
    hits as f64 / tokens.len() as f64 >= GROUNDED_SHARE
}
