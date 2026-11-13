//! Pure: checking a reply against the measured style and the channel register, and the code fixes applied
//! when the one repair call did not clear them. Checks never drop a reply.

use std::sync::LazyLock;

use crate::register::Register;
use crate::style::{Metrics, NONE, greeting_pattern, length_window, sign_off_of};
use crate::textstats::{self as ts, BULLET, EMOJI};
use crate::util::{Regex, re, replace_all, split};

pub const CHECKED: &[&str] = &[
    "length",
    "greeting",
    "sign-off",
    "contractions",
    "exclamations",
    "emoji",
    "bullets",
    "case",
    "em dash",
];
static PARAGRAPHS: LazyLock<Regex> = LazyLock::new(|| re(r"\n\s*\n"));
static PUNCT_END: LazyLock<Regex> = LazyLock::new(|| re(r"[\s.,!]+$"));
static SPACES: LazyLock<Regex> = LazyLock::new(|| re(r"\s+"));
static RUNS: LazyLock<Regex> = LazyLock::new(|| re(r"[ \t]{2,}"));

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Violation {
    /// One of CHECKED, or "cap" / "paragraphs" for the channel's own limits.
    pub check: &'static str,
    /// Worded for the repair call.
    pub message: String,
}

fn violation(check: &'static str, message: String) -> Violation {
    Violation { check, message }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Target {
    pub metrics: Metrics,
    pub register: &'static Register,
    /// The incoming sender's first name, for {first}.
    pub sender: String,
    /// The user asked for a different style this turn ("more formal", "shorter").
    pub relaxed: bool,
    pub short: bool,
}

pub fn expected_greeting(target: &Target) -> String {
    let name = if target.sender.is_empty() {
        "[name?]"
    } else {
        target.sender.as_str()
    };
    target.metrics.greeting.replace("{first}", name)
}

pub fn violations(body: &str, target: &Target) -> Vec<Violation> {
    let (m, reg) = (&target.metrics, target.register);
    let mut found = register_violations(body, reg);
    if !m.measured() || target.relaxed {
        return found;
    }
    let words = ts::word_count(body);
    let (low, high) = length_window(m, target.short);
    if !(low..=high).contains(&words) {
        found.push(violation(
            "length",
            format!(
                "the reply is {words} words; your examples average {} words: keep it between {low} and {high} words",
                m.avg_words
            ),
        ));
    }
    found.extend(greeting(body, target));
    found.extend(sign_off(body, target));
    found.extend(habits(body, m, reg, words));
    found
}

fn register_violations(body: &str, reg: &Register) -> Vec<Violation> {
    let mut found = Vec::new();
    let label = reg.label.to_lowercase();
    if let Some(cap) = reg.over_cap(body) {
        found.push(violation("cap", cap));
    }
    if let Some(max) = reg.max_paragraphs
        && split(&PARAGRAPHS, body.trim()).len() > max
    {
        found.push(violation(
            "paragraphs",
            format!("a {label} has at most {max} paragraph(s)"),
        ));
    }
    if !reg.sign_off && sign_off_of(body) != NONE {
        found.push(violation(
            "sign-off",
            format!("a {label} has no sign-off: remove the closing line"),
        ));
    }
    if !reg.bullets && ts::has_bullets(body) {
        found.push(violation(
            "bullets",
            format!("a {label} has no bullet points: use sentences"),
        ));
    }
    found
}

fn greeting(body: &str, target: &Target) -> Vec<Violation> {
    let m = &target.metrics;
    if !target.register.greeting {
        return Vec::new();
    }
    if m.greeting == NONE {
        if greeting_pattern(body) != NONE {
            return vec![violation(
                "greeting",
                "your examples open without a greeting: start straight in".into(),
            )];
        }
        return Vec::new();
    }
    let expected = expected_greeting(target);
    let ok = if target.sender.is_empty() {
        greeting_pattern(body).to_lowercase() == m.greeting.to_lowercase()
    } else {
        body.trim_start().starts_with(&expected)
    };
    if ok {
        Vec::new()
    } else {
        vec![violation(
            "greeting",
            format!("open with '{expected}' exactly, as your examples do"),
        )]
    }
}

fn sign_off(body: &str, target: &Target) -> Vec<Violation> {
    let (m, reg) = (&target.metrics, target.register);
    if !reg.sign_off {
        return Vec::new();
    }
    if m.sign_off == NONE {
        if sign_off_of(body) != NONE {
            return vec![violation(
                "sign-off",
                "your examples have no sign-off: end without one".into(),
            )];
        }
        return Vec::new();
    }
    if norm(body).ends_with(&norm(&m.sign_off)) {
        return Vec::new();
    }
    vec![violation(
        "sign-off",
        format!(
            "end with '{}' exactly, as your examples do",
            m.sign_off.replace('\n', " / ")
        ),
    )]
}

pub fn norm(text: &str) -> String {
    let collapsed = replace_all(&SPACES, &text.to_lowercase(), " ").into_owned();
    replace_all(&PUNCT_END, &collapsed, "").into_owned()
}

fn habits(body: &str, m: &Metrics, reg: &Register, words: usize) -> Vec<Violation> {
    let mut found = Vec::new();
    let count = ts::contractions(body);
    if m.contraction_rate >= 2.0 && count == 0 {
        found.push(violation(
            "contractions",
            "your examples use contractions (it's, can't); the reply has none".into(),
        ));
    }
    if m.contraction_rate == 0.0 && count > 0 {
        found.push(violation(
            "contractions",
            "your examples never use contractions: write them out".into(),
        ));
    }
    let allowed = ts::allowed_exclamations(m.exclaim_rate, words);
    let bangs = body.matches('!').count();
    if bangs > allowed {
        found.push(violation(
            "exclamations",
            format!("use at most {allowed} exclamation mark(s); the reply has {bangs}"),
        ));
    }
    if ts::has_emoji(body) && !m.emoji {
        found.push(violation(
            "emoji",
            "your examples have no emoji: remove them".into(),
        ));
    }
    if reg.bullets && ts::has_bullets(body) && !m.bullets {
        found.push(violation(
            "bullets",
            "your examples never use bullet points: use sentences".into(),
        ));
    }
    if m.lowercase_start && body.trim().chars().next().is_some_and(char::is_uppercase) {
        found.push(violation(
            "case",
            "your examples start with a lowercase letter".into(),
        ));
    }
    if m.avoids_em_dash && body.contains('—') {
        found.push(violation(
            "em dash",
            "your examples never use em dashes: use a comma or a full stop".into(),
        ));
    }
    found
}

/// What code can fix: emoji, em dashes, extra exclamation marks, bullets, case, a forbidden sign-off,
/// paragraphs on a text message. Length, greeting and contractions stay as written (and are noted).
pub fn fix(body: &str, target: &Target) -> String {
    let (m, reg) = (&target.metrics, target.register);
    let mut out = body.to_string();
    if !reg.sign_off && sign_off_of(&out) != NONE {
        out = drop_sign_off(&out);
    }
    if !m.emoji {
        let stripped = replace_all(&EMOJI, &out, "").into_owned();
        out = replace_all(&RUNS, &stripped, " ").into_owned();
    }
    if !reg.bullets || (m.measured() && !m.bullets) {
        out = replace_all(&BULLET, &out, "${1}").into_owned();
    }
    if reg.max_paragraphs == Some(1) {
        out = split(&PARAGRAPHS, out.trim())
            .iter()
            .map(|p| p.trim().replace('\n', " "))
            .collect::<Vec<_>>()
            .join(" ");
    }
    if m.measured() && !target.relaxed {
        out = fix_habits(&out, m);
    }
    out.trim().to_string()
}

fn fix_habits(body: &str, m: &Metrics) -> String {
    let mut out = if m.avoids_em_dash {
        body.replace(" — ", ", ").replace('—', ", ")
    } else {
        body.to_string()
    };
    let allowed = ts::allowed_exclamations(m.exclaim_rate, ts::word_count(&out));
    while out.matches('!').count() > allowed {
        let Some(idx) = out.rfind('!') else { break };
        out.replace_range(idx..idx + 1, ".");
    }
    if m.lowercase_start && out.chars().next().is_some_and(char::is_uppercase) {
        let mut chars = out.chars();
        if let Some(first) = chars.next() {
            out = first.to_lowercase().chain(chars).collect();
        }
    }
    out
}

fn drop_sign_off(body: &str) -> String {
    let trimmed = body.trim_end();
    let lines: Vec<&str> = trimmed.split('\n').collect();
    let closing = sign_off_of(body);
    if closing.contains('\n') && lines.len() > 2 {
        return lines[..lines.len() - 2].join("\n").trim_end().to_string();
    }
    if lines.len() > 1 && lines.last().is_some_and(|l| l.trim() == closing) {
        return lines[..lines.len() - 1].join("\n").trim_end().to_string();
    }
    let mut cut = trimmed.len().saturating_sub(closing.len());
    while !trimmed.is_char_boundary(cut) {
        cut -= 1;
    }
    trimmed[..cut].trim_end().to_string()
}
