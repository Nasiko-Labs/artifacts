//! Pure: style metrics measured in code from the user's past replies. The metrics are the truth the reply
//! is checked against; the model sees only the plain-word descriptors built from them, never the examples.

use std::collections::HashMap;
use std::sync::LazyLock;

use crate::textstats as ts;
use crate::util::{Regex, captures, find, group, is_match, lines, nth, re, round_even, round1};

pub const NONE: &str = "none";
pub const DEFAULT_GREETING: &str = "Hi {first},";
pub const DEFAULT_SIGN_OFF: &str = "Thanks,\n[your name?]";
pub const MAX_SIGN_OFF_WORDS: usize = 4;

const CLOSER: &str = concat!(
    r"(?:thanks|thank you|many thanks|cheers|best|best regards|kind regards|regards|sincerely|",
    r"talk soon|take care|ta|thx|warmly|speak soon)[,!.]?"
);

static GREETING: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)^(?P<word>hi|hey|hello|hiya|dear|morning|good morning|yo|heya)\b",
        r"(?:[ \t]+(?P<who>[A-Za-z][\w'’-]*))?[ \t]*(?P<punct>[,!]|(?=\n)|$)"
    ))
});
const GROUP_WORDS: &[&str] = &[
    "all", "team", "folks", "everyone", "there", "guys", "y'all", "both",
];
static CLOSER_FULL: LazyLock<Regex> = LazyLock::new(|| re(&format!(r"(?i)\A{CLOSER}\z")));
static CLOSER_START: LazyLock<Regex> = LazyLock::new(|| re(&format!(r"(?i)\A{CLOSER}")));
static INLINE_SIGN_OFF: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)(?:^|(?<=[.!?])\s+)({CLOSER}(?:[ \t]+[A-Z][\w.]*)?\.?)\s*$"
    ))
});
static FORMAL_MARKS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\bdear\b|\bbest regards\b|\bsincerely\b|\bkind regards\b"));
static NEUTRAL_MARKS: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\bhi\b|\bthanks\b"));
static CASUAL_MARKS: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\bhey\b|\bcheers\b"));

pub fn formality_name(level: u8) -> &'static str {
    match level {
        1 => "very casual",
        2 => "casual",
        4 => "formal",
        5 => "very formal",
        _ => "neutral",
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Metrics {
    /// 0: the neutral default, nothing measured.
    pub examples: usize,
    pub avg_words: usize,
    pub avg_sentence_words: usize,
    /// Per 100 words.
    pub contraction_rate: f64,
    pub exclaim_rate: f64,
    pub question_rate: f64,
    pub emoji: bool,
    pub bullets: bool,
    /// With `{first}` for the recipient's first name, or "none".
    pub greeting: String,
    /// A line break as `\n`, or "none".
    pub sign_off: String,
    pub lowercase_start: bool,
    pub formality: u8,
    pub top_openers: Vec<String>,
    pub avoids_em_dash: bool,
}

impl Default for Metrics {
    fn default() -> Self {
        Self {
            examples: 0,
            avg_words: 0,
            avg_sentence_words: 0,
            contraction_rate: 0.0,
            exclaim_rate: 0.0,
            question_rate: 0.0,
            emoji: false,
            bullets: false,
            greeting: DEFAULT_GREETING.into(),
            sign_off: DEFAULT_SIGN_OFF.into(),
            lowercase_start: false,
            formality: 3,
            top_openers: Vec::new(),
            avoids_em_dash: false,
        }
    }
}

impl Metrics {
    pub fn measured(&self) -> bool {
        self.examples > 0
    }

    /// "sign off as A.": keep the measured closer word, use the given name.
    pub fn with_sign_off(&self, name: &str) -> Metrics {
        if name.is_empty() {
            return self.clone();
        }
        let closer = if self.sign_off != NONE {
            let first_line = self.sign_off.split('\n').next().unwrap_or("");
            first_line.split(' ').next().unwrap_or("").to_string()
        } else {
            "Thanks,".to_string()
        };
        let closer = if closer.ends_with(',') {
            closer
        } else {
            format!("{},", closer.trim_end_matches(['.', '!']))
        };
        Metrics {
            sign_off: format!("{closer} {}", name.trim()),
            ..self.clone()
        }
    }
}

pub fn measure(examples: &[String]) -> Metrics {
    let texts: Vec<&str> = examples
        .iter()
        .map(|e| e.trim())
        .filter(|e| !e.is_empty())
        .collect();
    if texts.is_empty() {
        return Metrics::default();
    }
    let n = texts.len() as f64;
    let total = texts
        .iter()
        .map(|t| ts::word_count(t))
        .sum::<usize>()
        .max(1) as f64;
    let rate = |count: usize| round1(100.0 * count as f64 / total);
    let sentences: usize = texts.iter().map(|t| ts::sentence_count(t)).sum();
    let greetings: Vec<String> = texts.iter().map(|t| greeting_pattern(t)).collect();
    let sign_offs: Vec<String> = texts.iter().map(|t| sign_off_of(t)).collect();
    let metrics = Metrics {
        examples: texts.len(),
        avg_words: round_even(total / n).max(0) as usize,
        avg_sentence_words: round_even(total / sentences.max(1) as f64).max(0) as usize,
        contraction_rate: rate(texts.iter().map(|t| ts::contractions(t)).sum()),
        exclaim_rate: rate(texts.iter().map(|t| t.matches('!').count()).sum()),
        question_rate: rate(texts.iter().map(|t| t.matches('?').count()).sum()),
        emoji: texts.iter().any(|t| ts::has_emoji(t)),
        bullets: texts.iter().any(|t| ts::has_bullets(t)),
        greeting: most_common(&greetings),
        sign_off: most_common(&sign_offs),
        lowercase_start: texts
            .iter()
            .all(|t| t.chars().next().is_some_and(char::is_lowercase)),
        formality: 3,
        top_openers: texts
            .iter()
            .map(|t| t.split_whitespace().take(2).collect::<Vec<_>>().join(" "))
            .collect(),
        avoids_em_dash: !texts.iter().any(|t| t.contains('—')),
    };
    let level = formality(&texts, &metrics);
    Metrics {
        formality: level,
        ..metrics
    }
}

/// 1-5 from rules, in order (approximate): many contractions with ! or emoji -> 2; no contractions and
/// a formal greeting/closer -> 5; Hi/Thanks -> 3; Hey/cheers -> 2; else 3.
pub fn formality(texts: &[&str], m: &Metrics) -> u8 {
    let joined = texts.join("\n");
    if m.contraction_rate > 3.0 && (m.exclaim_rate > 0.0 || m.emoji) {
        return 2;
    }
    if m.contraction_rate == 0.0 && is_match(&FORMAL_MARKS, &joined) {
        return 5;
    }
    if is_match(&NEUTRAL_MARKS, &joined) {
        return 3;
    }
    if is_match(&CASUAL_MARKS, &joined) {
        return 2;
    }
    3
}

pub fn greeting_pattern(text: &str) -> String {
    let Some(caps) = captures(&GREETING, text.trim()) else {
        return NONE.into();
    };
    let (word, who, punct) = (
        group(&caps, "word"),
        group(&caps, "who"),
        group(&caps, "punct"),
    );
    if !who.is_empty() && GROUP_WORDS.contains(&who.to_lowercase().as_str()) {
        return format!("{word} {who}{punct}");
    }
    if !who.is_empty() {
        return format!("{word} {{first}}{punct}");
    }
    format!("{word}{punct}")
}

/// The closing line(s): the last line (or the last two, closer then name) when short, or a closer at
/// the end of a one-line message ("... works. Cheers, A").
pub fn sign_off_of(text: &str) -> String {
    let all: Vec<&str> = lines(text.trim())
        .into_iter()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    if all.len() >= 2 {
        let (last, before) = (all[all.len() - 1], all[all.len() - 2]);
        if before.split_whitespace().count() <= MAX_SIGN_OFF_WORDS
            && is_match(&CLOSER_FULL, before)
            && last.split_whitespace().count() <= 2
        {
            return format!("{before}\n{last}");
        }
        if last.split_whitespace().count() <= MAX_SIGN_OFF_WORDS
            && find(&CLOSER_START, last).is_some()
        {
            return last.to_string();
        }
    }
    let last_line = all.last().copied().unwrap_or("");
    if let Some(caps) = captures(&INLINE_SIGN_OFF, last_line) {
        let found = nth(&caps, 1);
        if found.split_whitespace().count() <= MAX_SIGN_OFF_WORDS {
            return found.trim().to_string();
        }
    }
    NONE.into()
}

fn most_common(values: &[String]) -> String {
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for v in values {
        *counts.entry(v.as_str()).or_default() += 1;
    }
    let best = counts.values().copied().max().unwrap_or(0);
    values
        .iter()
        .find(|v| counts.get(v.as_str()) == Some(&best))
        .cloned()
        .unwrap_or_else(|| NONE.into())
}

/// The style in plain words and numbers, for the model.
pub fn descriptors(m: &Metrics, short: bool) -> String {
    let lines: Vec<String> = if !m.measured() {
        vec![
            "formality: 3/5 (neutral)".into(),
            format!("greeting: {DEFAULT_GREETING}"),
            format!("sign-off: {}", DEFAULT_SIGN_OFF.replace('\n', " / ")),
            "no emoji".into(),
        ]
    } else {
        let (low, high) = length_window(m, short);
        let mut out = vec![
            format!(
                "formality: {}/5 ({}, approx.)",
                m.formality,
                formality_name(m.formality)
            ),
            format!(
                "length: about {} words (between {low} and {high})",
                m.avg_words
            ),
            format!("about {} words per sentence", m.avg_sentence_words),
            format!(
                "greeting: {}",
                if m.greeting != NONE {
                    m.greeting.as_str()
                } else {
                    "none, start straight in"
                }
            ),
            format!(
                "sign-off: {}",
                if m.sign_off != NONE {
                    m.sign_off.replace('\n', " / ")
                } else {
                    "none".into()
                }
            ),
            contraction_words(m),
            format!(
                "exclamation marks: {}",
                if m.exclaim_rate == 0.0 {
                    "none".to_string()
                } else {
                    format!("about {} per 100 words", m.exclaim_rate)
                }
            ),
            format!(
                "emoji: {}",
                if m.emoji { "yes, sometimes" } else { "never" }
            ),
            format!(
                "bullet points: {}",
                if m.bullets { "fine" } else { "never" }
            ),
        ];
        if m.lowercase_start {
            out.push("starts with a lowercase letter".into());
        }
        if m.avoids_em_dash {
            out.push("never uses em dashes".into());
        }
        out
    };
    lines
        .iter()
        .map(|l| format!("- {l}"))
        .collect::<Vec<_>>()
        .join("\n")
}

/// "1 per N words" for a contraction rate per 100 words.
pub fn per_words(rate: f64) -> i64 {
    round_even(100.0 / rate).max(1)
}

fn contraction_words(m: &Metrics) -> String {
    if m.contraction_rate == 0.0 {
        return "contractions: none (write 'do not', not 'don't')".into();
    }
    if m.contraction_rate >= 2.0 {
        return format!(
            "contractions: yes (about 1 per {} words)",
            per_words(m.contraction_rate)
        );
    }
    "contractions: occasionally".into()
}

pub fn length_window(m: &Metrics, short: bool) -> (usize, usize) {
    let (low, high) = ts::length_bounds(m.avg_words);
    (if short { 0 } else { low }, high)
}
