//! Pure: what the user gave this turn, read from the A2A message, and how a new request splits into goal,
//! spec, granularity, team size and deadline.
//!
//! - The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
//!   instructions (rules), never goal text and never facts about the user's project.
//! - With chat history the first part is "{history}\n\nCurrent message: {text}"; the text after the last
//!   marker is this turn's request and the history before it is parsed into turns (history.rs): this
//!   agent keeps no state, so the earlier plan is read back from those turns (recall.rs).

use std::sync::LazyLock;

use a2a::{Message, PartContent};
use regex::Regex;

use crate::fence::strip_markers;
use crate::history::{self, Turn};
use crate::util::{char_len, first_group, lines, re, skip_chars, take_chars};

pub const MAX_GOAL_CHARS: usize = 2_000;
/// The chat paste limit; above it the rest is not read and the reply says so.
pub const MAX_SPEC_CHARS: usize = 100_000;
pub const CHUNK_THRESHOLD_CHARS: usize = 12_000;
pub const MAX_CHUNKS: usize = 4;
pub const MIN_GOAL_WORDS: usize = 4;
pub const INSTRUCTION_LINE_MAX_CHARS: usize = 200;
pub const MAX_TEAM_SIZE: i64 = 20;

pub const NUMBER_WORDS: &[(&str, i64)] = &[
    ("one", 1),
    ("two", 2),
    ("three", 3),
    ("four", 4),
    ("five", 5),
    ("six", 6),
    ("seven", 7),
    ("eight", 8),
    ("nine", 9),
    ("ten", 10),
];

/// Task-count bands by granularity.
pub fn band(granularity: &str) -> (usize, usize) {
    match granularity {
        "coarse" => (3, 10),
        "fine" => (25, 40),
        _ => (10, 25),
    }
}

/// A number word or digits (as matched by `number_alt`).
pub fn number(raw: &str) -> i64 {
    let lowered = raw.to_lowercase();
    NUMBER_WORDS
        .iter()
        .find(|(w, _)| *w == lowered)
        .map(|(_, n)| *n)
        .unwrap_or_else(|| crate::util::num(raw))
}

/// `(\d{1,N}|one|two|...|ten)` as a capture group.
pub fn number_alt(max_digits: usize) -> String {
    let words: Vec<&str> = NUMBER_WORDS.iter().map(|(w, _)| *w).collect();
    format!(r"(\d{{1,{max_digits}}}|{})", words.join("|"))
}

const MONTH: &str = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)";
const DAY: &str = r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day";
const PERIOD: &str = r"(?:week|month|quarter|sprint|year)";

static JSON_WORDS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\bas json\b|\bjson only\b|\bin json\b"));
static COARSE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)high[- ]level|\bcoarse\b|\brough(?:ly)?\b|\boverview\b|\bbig[- ]picture\b")
});
static FINE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)\bdetailed\b|fine[- ]grained|\bgranular\b|\bin detail\b|\bstep[- ]by[- ]step\b")
});
static TEAM: LazyLock<Regex> = LazyLock::new(|| {
    let n = number_alt(2);
    re(&format!(
        r"(?i)\b(?:team of|we are|we're|there are|with|for|by) {n} ?(?:people|persons?|engineers?|devs?|developers?|of us|folks|designers?|members?)\b|\bteam of {n}\b"
    ))
});
static DEADLINE: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"\b(?:by|before|due|deadline:?|until|no later than)\s+(?:the\s+)?(?:end of\s+)?(?:\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}|{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?|{MONTH}|\d{{4}}-\d{{2}}-\d{{2}}|{DAY}|Q[1-4](?:\s+\d{{4}})?|(?:next|this)\s+{PERIOD}|{PERIOD})\b"
    ))
});
/// A timebox as typed ("a 2-week plan", "in 3 weeks"): kept as the deadline text, never converted to dates.
static TIMEBOX: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)\b(?:(?:in|within|over) )?{}[- ]?(?:weeks?|days?|months?|sprints?)\b",
        number_alt(2)
    ))
});
static PLAN_ASK: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\b(?:plan|make a .{0,30}plan|break (?:it )?down|tasks?|phases?)\b"));
static PARAGRAPH: LazyLock<Regex> = LazyLock::new(|| re(r"\n\s*\n"));

/// One turn's input: the user's words, platform policy parts, whether JSON output was asked, and the
/// platform's history slice (the agent's only memory of earlier turns).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Incoming {
    pub text: String,
    pub policy: Vec<String>,
    pub wants_json: bool,
    pub history: Vec<Turn>,
}

impl Incoming {
    pub fn text(text: &str) -> Self {
        Self {
            text: text.to_string(),
            ..Self::default()
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Brief {
    pub goal: String,
    pub spec: String,
    pub granularity: String,
    pub team_size: Option<i64>,
    pub deadline: String,
    /// Characters beyond MAX_SPEC_CHARS that were not read.
    pub spec_cut: usize,
}

impl Brief {
    /// Everything the user typed for this plan: what assumptions and owners are checked against.
    pub fn source(&self) -> String {
        format!("{}\n{}", self.goal, self.spec).trim().to_string()
    }
}

/// The first text part is the user's; later text parts are policy; metadata may ask for JSON.
pub fn read_message(message: Option<&Message>) -> Incoming {
    let Some(message) = message else {
        return Incoming::default();
    };
    let texts: Vec<&str> = message
        .parts
        .iter()
        .filter_map(|p| match &p.content {
            PartContent::Text(text) => Some(text.as_str()),
            _ => None,
        })
        .collect();
    let (block, current) = texts.first().map_or(("", ""), |t| history::split(t));
    let user = current.trim().to_string();
    let output = message
        .metadata
        .as_ref()
        .and_then(|m| m.get("output"))
        .map(|v| v.as_str().map_or_else(|| v.to_string(), str::to_string))
        .unwrap_or_default();
    let wants = output.to_lowercase() == "json" || JSON_WORDS.is_match(&user);
    Incoming {
        policy: texts
            .iter()
            .skip(1)
            .filter(|t| !t.trim().is_empty())
            .map(|t| t.to_string())
            .collect(),
        wants_json: wants,
        history: history::parse(block),
        text: user,
    }
}

pub fn word_count(text: &str) -> usize {
    text.split_whitespace().count()
}

pub fn wants_json(text: &str) -> bool {
    JSON_WORDS.is_match(text)
}

pub fn granularity(text: &str) -> &'static str {
    if FINE.is_match(text) {
        "fine"
    } else if COARSE.is_match(text) {
        "coarse"
    } else {
        "normal"
    }
}

/// The team size the text states ("we are 3", "for one engineer"), 1-20.
pub fn team_size(text: &str) -> Option<i64> {
    let caps = TEAM.captures(text)?;
    let value = number(first_group(&caps));
    (1..=MAX_TEAM_SIZE).contains(&value).then_some(value)
}

/// The deadline exactly as typed (never converted to a date).
pub fn deadline(text: &str) -> String {
    DEADLINE
        .find(text)
        .or_else(|| TIMEBOX.find(text))
        .map_or(String::new(), |m| m.as_str().trim().to_string())
}

/// A change that re-plans around a team size or a timebox/deadline ("make it a 2-week plan for one
/// engineer"): always in scope for a plan, whatever the model says.
pub fn is_replan(text: &str) -> bool {
    team_size(text).is_some() || !deadline(text).is_empty()
}

pub fn asks_for_plan(text: &str) -> bool {
    PLAN_ASK.is_match(text)
}

pub fn is_vague(brief: &Brief) -> bool {
    word_count(&brief.goal) < MIN_GOAL_WORDS && brief.spec.trim().is_empty()
}

/// Goal = the whole message when short; else its first paragraph (plus a short closing instruction
/// line); the rest is the spec. Granularity, team size and deadline are read from the whole text.
pub fn parse_brief(text: &str) -> Brief {
    let stripped = strip_markers(text);
    let clean = stripped.trim();
    let (mut goal, spec) = if char_len(clean) <= MAX_GOAL_CHARS {
        (clean.to_string(), String::new())
    } else {
        let (mut head, mut rest) = clean.split_once("\n\n").unwrap_or((clean, ""));
        if char_len(head) > MAX_GOAL_CHARS {
            head = take_chars(clean, MAX_GOAL_CHARS);
            rest = skip_chars(clean, MAX_GOAL_CHARS);
        }
        (head.trim().to_string(), rest.trim().to_string())
    };
    let last = lines(&spec).last().map_or("", |l| l.trim());
    if !last.is_empty() && char_len(last) <= INSTRUCTION_LINE_MAX_CHARS && PLAN_ASK.is_match(last) {
        goal = format!("{goal}\n{last}");
    }
    let cut = char_len(&spec).saturating_sub(MAX_SPEC_CHARS);
    Brief {
        goal,
        spec: take_chars(&spec, MAX_SPEC_CHARS).to_string(),
        granularity: granularity(clean).to_string(),
        team_size: team_size(clean),
        deadline: deadline(clean),
        spec_cut: cut,
    }
}

/// A spec over 12,000 chars split by paragraphs (then lines) into at most 4 chunks; nothing is dropped.
pub fn chunk(spec: &str) -> Vec<String> {
    let len = char_len(spec);
    if len <= CHUNK_THRESHOLD_CHARS {
        return if spec.trim().is_empty() {
            Vec::new()
        } else {
            vec![spec.to_string()]
        };
    }
    let count = MAX_CHUNKS.min(len.div_ceil(CHUNK_THRESHOLD_CHARS));
    let target = len.div_ceil(count);
    let mut chunks: Vec<String> = Vec::new();
    let mut current = String::new();
    for unit in units(spec, target) {
        if !current.is_empty()
            && char_len(&current) + char_len(&unit) > target
            && chunks.len() < count - 1
        {
            chunks.push(std::mem::take(&mut current));
        }
        current = if current.is_empty() {
            unit
        } else {
            format!("{current}\n\n{unit}")
        };
    }
    if !current.is_empty() {
        chunks.push(current);
    }
    chunks
}

fn units(spec: &str, target: usize) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for para in PARAGRAPH.split(spec) {
        if char_len(para) <= target {
            out.push(para.to_string());
            continue;
        }
        for line in lines(para) {
            let chars: Vec<char> = line.chars().collect();
            out.extend(
                chars
                    .chunks(target.max(1))
                    .map(|c| c.iter().collect::<String>()),
            );
        }
    }
    out.retain(|u| !u.trim().is_empty());
    out
}
