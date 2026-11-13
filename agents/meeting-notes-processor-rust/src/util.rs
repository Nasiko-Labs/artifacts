//! Small text helpers that never panic on user data: every cut is on a char boundary.

use std::sync::LazyLock;

use regex::Regex;

/// Line numbers are clamped so later arithmetic (`line - 12`, `line + 12`) can never overflow.
pub const MAX_LINE_NUMBER: i64 = 1_000_000_000;

/// Compile a pattern written in this crate. Patterns are constants, so a failure is a bug caught by
/// the tests; it falls back to a regex that matches nothing instead of panicking at runtime.
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

/// A decimal number from a regex capture of ASCII digits, clamped to a safe range.
pub fn num(digits: &str) -> i64 {
    digits
        .parse::<i64>()
        .unwrap_or(MAX_LINE_NUMBER)
        .clamp(0, MAX_LINE_NUMBER)
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

/// A named capture group's text, or "" when the group did not take part in the match.
pub fn group<'t>(caps: &regex::Captures<'t>, name: &str) -> &'t str {
    caps.name(name).map_or("", |m| m.as_str())
}

/// Strip any of `chars` from the start of `text` (Python `lstrip(chars)`).
pub fn lstrip_chars<'a>(text: &'a str, chars: &str) -> &'a str {
    text.trim_start_matches(|c| chars.contains(c))
}

/// Strip any of `chars` from both ends of `text` (Python `strip(chars)`).
pub fn strip_chars<'a>(text: &'a str, chars: &str) -> &'a str {
    text.trim_matches(|c| chars.contains(c))
}

/// Whether the first char of `line` is whitespace (Python `line[:1].isspace()`).
pub fn starts_with_space(line: &str) -> bool {
    line.chars().next().is_some_and(char::is_whitespace)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cuts_are_char_safe() {
        assert_eq!(take_chars("héllo", 2), "hé");
        assert_eq!(last_chars("héllo", 4), "éllo");
        assert_eq!(take_chars("ab", 9), "ab");
        assert_eq!(lines("a\r\nb\rc\n"), vec!["a", "b", "c"]);
        assert_eq!(thousands(100_000), "100,000");
        assert_eq!(num("99999999999999999999"), MAX_LINE_NUMBER);
        assert!(Regex::new(r"[^\s\S]").is_ok_and(|never| !never.is_match("anything")));
    }
}
