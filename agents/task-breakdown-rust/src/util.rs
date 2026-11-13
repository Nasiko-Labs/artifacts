//! Small text helpers that never panic on user data: every cut is on a char boundary.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

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

/// Everything after the first `n` chars of `text`.
pub fn skip_chars(text: &str, n: usize) -> &str {
    match text.char_indices().nth(n) {
        Some((idx, _)) => &text[idx..],
        None => "",
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

/// A count from a regex capture of ASCII digits; huge values saturate instead of failing.
pub fn num(digits: &str) -> i64 {
    digits.parse::<i64>().unwrap_or(i64::MAX / 4)
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

/// Capture group `i` as text, or "" when it did not take part in the match.
pub fn cap<'t>(caps: &regex::Captures<'t>, i: usize) -> &'t str {
    caps.get(i).map_or("", |m| m.as_str())
}

/// The first capture group (after group 0) that took part in the match.
pub fn first_group<'t>(caps: &regex::Captures<'t>) -> &'t str {
    caps.iter()
        .skip(1)
        .flatten()
        .map(|m| m.as_str())
        .find(|s| !s.is_empty())
        .unwrap_or("")
}

/// Python's `repr()` of a str, for problem texts the model reads ('HUGE').
pub fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// `json.dumps(value, ensure_ascii=False)`: compact JSON with Python's ", " and ": " separators.
pub fn py_dumps(value: &Value) -> String {
    match value {
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(py_dumps).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("{}: {}", Value::String(k.clone()), py_dumps(v)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
        other => other.to_string(),
    }
}

/// Order-preserving dedupe (`dict.fromkeys`).
pub fn dedupe(items: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for item in items {
        if !out.contains(&item) {
            out.push(item);
        }
    }
    out
}

/// Python's `round()` to an integer: halves go to the even neighbour.
pub fn round_half_even(x: f64) -> i64 {
    let floor = x.floor();
    let diff = x - floor;
    let rounded = if (diff - 0.5).abs() < 1e-12 {
        if (floor as i64) % 2 == 0 {
            floor
        } else {
            floor + 1.0
        }
    } else {
        x.round()
    };
    rounded as i64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helpers_are_char_safe_and_python_like() {
        assert_eq!(take_chars("héllo", 2), "hé");
        assert_eq!(skip_chars("héllo", 2), "llo");
        assert_eq!(last_chars("héllo", 4), "éllo");
        assert_eq!(lines("a\r\nb\rc\n"), vec!["a", "b", "c"]);
        assert_eq!(thousands(100_000), "100,000");
        assert_eq!(py_repr("HUGE"), "'HUGE'");
        assert_eq!(py_repr("it's"), "\"it's\"");
        assert_eq!(
            py_dumps(&serde_json::json!({"a": [1, "x"], "b": null})),
            r#"{"a": [1, "x"], "b": null}"#
        );
        assert_eq!(round_half_even(66.666), 67);
        assert_eq!(round_half_even(12.5), 12);
        assert_eq!(round_half_even(13.5), 14);
    }
}
