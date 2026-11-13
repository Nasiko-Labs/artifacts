//! Small helpers that never panic on user data: a regex wrapper with Python `re` semantics (lookaround,
//! backtracking) whose errors read as "no match", and char-safe text cuts.

use std::borrow::Cow;
use std::sync::LazyLock;

pub use fancy_regex::{Captures, Match, Regex};

/// Compile a pattern written in this crate. Patterns are constants, so a failure is a bug caught by
/// the tests; in release it falls back to a regex that matches nothing instead of panicking.
pub fn re(pattern: &str) -> Regex {
    Regex::new(pattern).unwrap_or_else(|err| {
        if cfg!(debug_assertions) {
            panic!("bad built-in pattern {pattern:?}: {err}");
        }
        tracing::error!("bad built-in pattern {pattern:?}: {err}");
        static NEVER: LazyLock<Regex> = LazyLock::new(|| {
            Regex::new(r"[^\s\S]").unwrap_or_else(|_| unreachable!("constant pattern"))
        });
        NEVER.clone()
    })
}

pub fn is_match(rx: &Regex, text: &str) -> bool {
    rx.is_match(text).unwrap_or(false)
}

pub fn find<'t>(rx: &Regex, text: &'t str) -> Option<Match<'t>> {
    rx.find(text).ok().flatten()
}

pub fn captures<'t>(rx: &Regex, text: &'t str) -> Option<Captures<'t>> {
    rx.captures(text).ok().flatten()
}

pub fn find_iter<'t>(rx: &Regex, text: &'t str) -> Vec<Match<'t>> {
    rx.find_iter(text).filter_map(Result::ok).collect()
}

pub fn captures_iter<'t>(rx: &Regex, text: &'t str) -> Vec<Captures<'t>> {
    rx.captures_iter(text).filter_map(Result::ok).collect()
}

/// Every match starting at or after `pos` (Python `pattern.finditer(text, pos)`).
pub fn find_iter_from<'t>(rx: &Regex, text: &'t str, pos: usize) -> Vec<Match<'t>> {
    let mut out = Vec::new();
    let mut at = pos;
    while at <= text.len() {
        let Some(m) = rx.find_from_pos(text, at).ok().flatten() else {
            break;
        };
        at = if m.end() > m.start() {
            m.end()
        } else {
            next_boundary(text, m.end())
        };
        out.push(m);
    }
    out
}

fn next_boundary(text: &str, at: usize) -> usize {
    text[at..]
        .chars()
        .next()
        .map_or(text.len() + 1, |c| at + c.len_utf8())
}

/// `re.sub(pattern, rep, text)`; on a regex error the text is returned unchanged.
pub fn replace_all<'t>(rx: &Regex, text: &'t str, rep: &str) -> Cow<'t, str> {
    rx.try_replacen(text, 0, rep).unwrap_or(Cow::Borrowed(text))
}

/// `re.split(pattern, text)` for a pattern without capture groups.
pub fn split<'t>(rx: &Regex, text: &'t str) -> Vec<&'t str> {
    let mut out = Vec::new();
    let mut last = 0;
    for m in find_iter(rx, text) {
        if m.end() == m.start() && m.start() == last {
            continue;
        }
        out.push(&text[last..m.start()]);
        last = m.end();
    }
    out.push(&text[last..]);
    out
}

/// A named capture group's text, or "" when it did not take part.
pub fn group<'t>(caps: &Captures<'t>, name: &str) -> &'t str {
    caps.name(name).map_or("", |m| m.as_str())
}

/// A numbered capture group's text, or "".
pub fn nth<'t>(caps: &Captures<'t>, i: usize) -> &'t str {
    caps.get(i).map_or("", |m| m.as_str())
}

/// The first `n` chars of `text`.
pub fn take_chars(text: &str, n: usize) -> &str {
    match text.char_indices().nth(n) {
        Some((idx, _)) => &text[..idx],
        None => text,
    }
}

/// The last `n` chars of `text`.
pub fn last_chars(text: &str, n: usize) -> &str {
    let len = text.chars().count();
    if len <= n {
        return text;
    }
    match text.char_indices().nth(len - n) {
        Some((idx, _)) => &text[idx..],
        None => text,
    }
}

pub fn char_len(text: &str) -> usize {
    text.chars().count()
}

/// `str.splitlines()`-like: `\n`, `\r\n` and lone `\r` end a line; no trailing empty line.
pub fn lines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let bytes = text.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'\n' => {
                out.push(&text[start..i]);
                start = i + 1;
            }
            b'\r' => {
                out.push(&text[start..i]);
                if bytes.get(i + 1) == Some(&b'\n') {
                    i += 1;
                }
                start = i + 1;
            }
            _ => {}
        }
        i += 1;
    }
    if start < bytes.len() {
        out.push(&text[start..]);
    }
    out
}

/// Whitespace collapsed to single spaces and trimmed (`" ".join(text.split())`).
pub fn collapse_ws(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Strip any of `chars` from both ends of `text` (Python `strip(chars)`).
pub fn strip_chars<'a>(text: &'a str, chars: &str) -> &'a str {
    text.trim_matches(|c| chars.contains(c))
}

/// Strip any of `chars` from the end of `text` (Python `rstrip(chars)`).
pub fn rstrip_chars<'a>(text: &'a str, chars: &str) -> &'a str {
    text.trim_end_matches(|c| chars.contains(c))
}

/// The number of whitespace-separated words (`len(text.split())`).
pub fn split_count(text: &str) -> usize {
    text.split_whitespace().count()
}

/// Python `round(x)`: half to even.
pub fn round_even(x: f64) -> i64 {
    let r = x.round_ties_even();
    if r.is_finite() { r as i64 } else { 0 }
}

/// Python `round(x, 1)` (half to even on the scaled value).
pub fn round1(x: f64) -> f64 {
    (x * 10.0).round_ties_even() / 10.0
}

/// Python `str.capitalize()`.
pub fn capitalize(text: &str) -> String {
    let mut chars = text.chars();
    match chars.next() {
        Some(first) => first
            .to_uppercase()
            .chain(chars.flat_map(char::to_lowercase))
            .collect(),
        None => String::new(),
    }
}

/// The first char upper-cased, the rest unchanged (`s[:1].upper() + s[1:]`).
pub fn upper_first(text: &str) -> String {
    let mut chars = text.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().chain(chars).collect(),
        None => String::new(),
    }
}

/// The byte index just past the char starting at `at` (for `text[:start + 1]`).
pub fn after_char(text: &str, at: usize) -> usize {
    text.get(at..)
        .and_then(|rest| rest.chars().next())
        .map_or(text.len(), |c| at + c.len_utf8())
}

/// `1234567` -> `"1,234,567"`.
pub fn thousands(n: usize) -> String {
    let digits = n.to_string();
    let mut out = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(ch);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helpers_are_char_safe_and_python_like() {
        assert_eq!(take_chars("héllo", 2), "hé");
        assert_eq!(last_chars("héllo", 4), "éllo");
        assert_eq!(lines("a\r\nb\rc\n"), vec!["a", "b", "c"]);
        assert_eq!(round_even(8.5), 8);
        assert_eq!(round_even(9.5), 10);
        assert_eq!(round1(100.0 / 17.0), 5.9);
        assert_eq!(capitalize("pRIYA"), "Priya");
        assert_eq!(thousands(6000), "6,000");
        let rx = re(r"\n\s*\n");
        assert_eq!(split(&rx, "a\n\nb\n \nc"), vec!["a", "b", "c"]);
        assert_eq!(after_char("é,", 0), 2);
    }
}
