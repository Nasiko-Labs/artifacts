//! Pure: is a quote, a name or a number really in the transcript? Both sides are normalised the same way:
//! casefold, NFKC, straight quotes, plain dashes, single spaces, and spoken fillers ("um", "uh", "you
//! know") removed - for matching only; the quote shown is the model's, once it is found. Offsets are
//! bytes into the normalised text; every window is measured in chars.

use std::sync::LazyLock;

use regex::Regex;
use unicode_normalization::UnicodeNormalization;

use crate::util::{re, strip_chars};

pub const MIN_QUOTE_CHARS: usize = 12;
pub const ELLIPSIS_SPAN_CHARS: usize = 600;
const EDGE: &str = " .,;:!?\"'()[]-";
const TRAILING: &str = ".,;:!?)]}\"'";

static FILLER: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\b(?:um+|uh+|uhm+|erm+|er|hmm+|you know)\b,?"));
static WHITESPACE: LazyLock<Regex> = LazyLock::new(|| re(r"\s+"));
static ELLIPSIS: LazyLock<Regex> = LazyLock::new(|| re(r"\.{3,}|\[\s*\.{3}\s*\]"));
const WEEKDAYS: &str = "monday|tuesday|wednesday|thursday|friday|saturday|sunday";
const MONTHS: &str =
    "january|february|march|april|june|july|august|september|october|november|december";
static TOKEN: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        concat!(
            r"(?i)[\w.+-]+@[\w-]+(?:\.[\w-]+)+",                  // email
            r"|https?://\S+|www\.\S+",                            // url
            r"|[$€£¥₹]?\d[\d,.:]*(?:\s?(?:k|m|bn|b|%|percent))?", // number, money, percentage, clock time
            r"|\b(?:{w}|{m})\b"                                   // weekday, month
        ),
        w = WEEKDAYS,
        m = MONTHS
    ))
});
static MAY: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)may"));

fn lookalike(c: char) -> Option<&'static str> {
    Some(match c {
        '\u{2018}' | '\u{2019}' | '\u{201a}' | '\u{201b}' => "'",
        '\u{201c}' | '\u{201d}' | '\u{201e}' => "\"",
        '\u{2010}' | '\u{2011}' | '\u{2012}' | '\u{2013}' | '\u{2014}' | '\u{2015}'
        | '\u{2212}' => "-",
        '\u{2026}' => "...",
        '\u{00a0}' => " ",
        _ => return None,
    })
}

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Casefold, NFKC, straight quotes, plain dashes, no fillers, single spaces.
pub fn normalise(text: &str) -> String {
    let mut plain = String::with_capacity(text.len());
    for c in text.nfkc() {
        match lookalike(c) {
            Some(s) => plain.push_str(s),
            None => plain.push(c),
        }
    }
    let no_fillers = FILLER.replace_all(&plain, " ");
    WHITESPACE
        .replace_all(&no_fillers, " ")
        .trim()
        .to_lowercase()
}

/// The byte offset `n` chars after `at` (clamped to the end).
fn forward(text: &str, at: usize, n: usize) -> usize {
    text[at..]
        .char_indices()
        .nth(n)
        .map_or(text.len(), |(i, _)| at + i)
}

/// `hay.find(needle, start, end)` with the whole match inside `[start, end)`.
fn find_within(hay: &str, needle: &str, start: usize, end: usize) -> Option<usize> {
    let end = end.min(hay.len());
    if start > end {
        return None;
    }
    hay.get(start..end)?.find(needle).map(|i| start + i)
}

/// (start, end) of the quote in a normalised text, or None. An ellipsis splits it: each piece must be
/// found, in order, all within ELLIPSIS_SPAN_CHARS. A quote under MIN_QUOTE_CHARS never matches.
pub fn locate(quote: &str, norm_text: &str) -> Option<(usize, usize)> {
    let normal = normalise(quote);
    let pieces: Vec<&str> = ELLIPSIS
        .split(&normal)
        .map(|p| strip_chars(p, EDGE))
        .filter(|p| !p.is_empty())
        .collect();
    let total: usize = pieces.iter().map(|p| p.chars().count()).sum();
    let first = *pieces.first()?;
    if total < MIN_QUOTE_CHARS {
        return None;
    }
    let mut from = 0;
    while let Some(at) = find_within(norm_text, first, from, norm_text.len()) {
        let limit = forward(norm_text, at, ELLIPSIS_SPAN_CHARS);
        if let Some(end) = rest_in_order(&pieces[1..], norm_text, at + first.len(), limit) {
            return Some((at, end));
        }
        from = forward(norm_text, at, 1);
    }
    None
}

fn rest_in_order(
    pieces: &[&str],
    norm_text: &str,
    mut cursor: usize,
    limit: usize,
) -> Option<usize> {
    for piece in pieces {
        let found = find_within(norm_text, piece, cursor, limit)?;
        cursor = found + piece.len();
    }
    Some(cursor)
}

/// `value` occurs in the normalised text on word boundaries.
pub fn contains_words(value: &str, norm_text: &str) -> bool {
    let normal = normalise(value);
    let wanted = strip_chars(&normal, EDGE);
    if wanted.is_empty() {
        return false;
    }
    norm_text.match_indices(wanted).any(|(at, _)| {
        let before = norm_text[..at].chars().next_back();
        let after = norm_text[at + wanted.len()..].chars().next();
        !before.is_some_and(is_word) && !after.is_some_and(is_word)
    })
}

/// Every number, amount, percentage, date word, email and URL in `text`.
pub fn tokens(text: &str) -> Vec<String> {
    let mut found: Vec<String> = TOKEN
        .find_iter(text)
        .map(|m| {
            m.as_str()
                .trim_end_matches(|c| TRAILING.contains(c))
                .to_string()
        })
        .collect();
    found.extend(may_as_month(text));
    found
        .into_iter()
        .filter(|t| !t.is_empty() && t.chars().any(char::is_alphanumeric))
        .collect()
}

/// "may" only as a month next to a day: `may 3` or `3 may` (not "we may revisit").
fn may_as_month(text: &str) -> Vec<String> {
    MAY.find_iter(text)
        .filter(|m| {
            let before: Vec<char> = text[..m.start()].chars().rev().take(2).collect();
            let after = &text[m.end()..];
            let next = after.chars().next();
            let word_start = before.first().is_none_or(|c| !is_word(*c));
            let then_day = after.starts_with(char::is_whitespace)
                && after.trim_start().starts_with(|c: char| c.is_ascii_digit());
            let day_first =
                before.len() == 2 && before[0].is_whitespace() && before[1].is_ascii_digit();
            (word_start && then_day) || (day_first && next.is_none_or(|c| !is_word(c)))
        })
        .map(|m| m.as_str().to_string())
        .collect()
}

/// The tokens of `text` that do not appear in the normalised transcript.
pub fn ungrounded_tokens(text: &str, norm_source: &str) -> Vec<String> {
    tokens(text)
        .into_iter()
        .filter(|t| !norm_source.contains(&normalise(t)))
        .collect()
}
