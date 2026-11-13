//! Pure: protected spans are replaced by tokens ⟦P1⟧, ⟦P2⟧ ... before the model sees the text, and put
//! back after. Code, links, emails, mentions, placeholders, glossary "keep" terms and numbers with units
//! or separators are masked; plain integers in running text stay visible (the digit check covers them).

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::{Captures, Regex};

use crate::util::re;

const MONTH: &str = "(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)";

/// A protected-span pattern; `free_before` means the char before the match may not be a word char or one
/// of `@#&` (the look-behind of the mention pattern).
struct Pattern {
    regex: Regex,
    free_before: &'static str,
    word_bounded: bool,
}

fn pattern(regex: &str) -> Pattern {
    Pattern {
        regex: re(regex),
        free_before: "",
        word_bounded: false,
    }
}

static PATTERNS: LazyLock<Vec<Pattern>> = LazyLock::new(|| {
    vec![
        pattern(r"(?s)```.*?```"), // fenced code
        pattern(r"`[^`\n]+`"),     // inline code
        pattern(r#"\b(?:https?://|www\.)[^\s<>()"']*[^\s<>()"'.,;:!?]"#), // URLs
        pattern(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), // emails
        Pattern {
            regex: re(r"[@#][A-Za-z0-9_][\w.-]*\w|[@#][A-Za-z0-9_]"), // @mentions, #channels
            free_before: "@#&",
            word_bounded: false,
        },
        pattern(r"\{\{[^{}\n]{1,60}\}\}|\{[A-Za-z_]\w{0,40}\}|%\([a-z_]+\)[sd]|%[sd]"), // template placeholders
        pattern(r"\[[^\[\]\n]{1,40}\?\]"), // [date?] placeholders
        pattern(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"), // numeric dates
        pattern(&format!(
            r"\b{MONTH}\.? \d{{1,2}}(?:st|nd|rd|th)?\b(?:,? \d{{4}})?|\b\d{{1,2}} {MONTH}\b\.?(?: \d{{4}})?"
        )),
        pattern(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp]\.?[Mm]\b\.?)?"), // times
        pattern(r"[$€£¥₹]\s?\d[\d,.]*\d|[$€£¥₹]\s?\d|\b\d[\d,.]*\s?(?:USD|EUR|GBP|INR|JPY)\b"), // money
        pattern(r"\b\d+(?:[.,]\d+)?\s?%"), // percentages
        pattern(
            r"\b\d+(?:\.\d+)?\s?(?:kg|km|cm|mm|ms|GB|MB|KB|TB|px|°C|°F|mph|lbs?|oz|g|m|s|h|min)\b",
        ), // units
        pattern(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b"), // separators, decimals
    ]
});
/// Patterns before the glossary "keep" terms; the rest rank after them.
const BEFORE_KEEP: usize = 7;

static TOKEN_VARIANTS: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?:\u{27e6}|\[\[|【|〚)\s*[Pp]\s*([0-9]{1,4})\s*(?:\u{27e7}|\]\]|】|〛)")
});
pub static TOKEN: LazyLock<Regex> = LazyLock::new(|| re(r"\u{27e6}P([0-9]{1,4})\u{27e7}"));

pub fn token(n: usize) -> String {
    format!("\u{27e6}P{n}\u{27e7}")
}

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Every match of `p` in `text`, honouring its look-around: a rejected match retries one char later.
fn matches(p: &Pattern, text: &str) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    let mut pos = 0;
    while pos <= text.len() {
        let Some(m) = p.regex.find_at(text, pos) else {
            break;
        };
        let before = text[..m.start()].chars().next_back();
        let after = text[m.end()..].chars().next();
        let free = before.is_none_or(|c| !is_word(c) && !p.free_before.contains(c));
        let left_ok = (p.free_before.is_empty() && !p.word_bounded) || free;
        let right_ok = !p.word_bounded || after.is_none_or(|c| !is_word(c));
        if left_ok && right_ok && m.start() < m.end() {
            out.push((m.start(), m.end()));
            pos = m.end();
        } else {
            pos = m.start() + text[m.start()..].chars().next().map_or(1, char::len_utf8);
        }
    }
    out
}

/// (masked text, token -> original span). Earlier patterns win where spans overlap; glossary "keep"
/// terms rank after placeholders and before numbers. Tokens are numbered from `first`.
pub fn mask(text: &str, keep_terms: &[String], first: usize) -> (String, HashMap<String, String>) {
    let keep: Vec<Pattern> = keep_terms
        .iter()
        .filter(|t| !t.is_empty())
        .map(|t| Pattern {
            regex: re(&format!("(?i){}", regex::escape(t))),
            free_before: "",
            word_bounded: true,
        })
        .collect();
    let ordered = PATTERNS[..BEFORE_KEEP]
        .iter()
        .chain(&keep)
        .chain(&PATTERNS[BEFORE_KEEP..]);
    let mut taken: Vec<(usize, usize)> = Vec::new();
    for p in ordered {
        for (start, end) in matches(p, text) {
            if !taken.iter().any(|&(s, e)| start < e && s < end) {
                taken.push((start, end));
            }
        }
    }
    taken.sort_unstable();
    let mut spans = HashMap::new();
    let mut out = String::with_capacity(text.len());
    let mut cursor = 0;
    for (n, (start, end)) in taken.into_iter().enumerate() {
        let tok = token(first + n);
        out.push_str(&text[cursor..start]);
        out.push_str(&tok);
        spans.insert(tok, text[start..end].to_string());
        cursor = end;
    }
    out.push_str(&text[cursor..]);
    (out, spans)
}

/// Folds near-miss token spellings ([[P1]], 【P1】, ⟦ P1 ⟧) back to ⟦P1⟧.
pub fn normalize_tokens(text: &str) -> String {
    TOKEN_VARIANTS
        .replace_all(text, |c: &Captures| {
            let n = c.get(1).map_or(0, |m| m.as_str().parse().unwrap_or(0));
            token(n)
        })
        .into_owned()
}

/// The tokens in `text`, in order (with repeats), each in its canonical spelling.
pub fn tokens_in(text: &str) -> Vec<String> {
    TOKEN
        .captures_iter(text)
        .map(|c| token(c.get(1).map_or(0, |m| m.as_str().parse().unwrap_or(0))))
        .collect()
}

pub fn restore(text: &str, spans: &HashMap<String, String>) -> String {
    TOKEN
        .replace_all(text, |c: &Captures| {
            let whole = c.get(0).map_or("", |m| m.as_str());
            spans.get(whole).cloned().unwrap_or_default()
        })
        .into_owned()
}

pub fn without_tokens(text: &str) -> String {
    TOKEN.replace_all(text, " ").into_owned()
}
