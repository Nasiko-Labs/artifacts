//! Pure: a pasted transcript or raw notes, parsed into numbered turns in code.
//!
//! Formats are tried in order; the first that yields at least MIN_TURNS turns wins:
//!
//! ```text
//! subtitle cues (VTT / SRT)         speaker from <v Name> or a leading "Name:", time from the cue start
//! "[00:12:03] Name: text", "Name (12:03): text"
//! "Name 00:12:03" on its own line   the text on the lines after it
//! "00:12 Name" on its own line      the text on the lines after it
//! "Name: text"
//! raw notes                          every line or bullet is a turn by "Unknown"
//! ```
//!
//! Same-speaker turns within MERGE_GAP_SECS are merged; ids T1..Tn are given last.

use std::sync::LazyLock;

use regex::Regex;

use crate::items::{Turn, UNKNOWN};
use crate::timecode::{show_time, to_secs};
use crate::util::{collapse_ws, re, take_chars};

pub const MIN_TURNS: usize = 5;
pub const MERGE_GAP_SECS: i64 = 60;
const NAME: &str = r"[A-Z][\w.'’-]*(?: [A-Z0-9][\w.'’-]*){0,3}";
const TIME: &str = r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?";
const LABELS: &[&str] = &[
    "title",
    "date",
    "meeting",
    "subject",
    "attendees",
    "participants",
    "agenda",
    "note",
    "notes",
    "action items",
    "actions",
    "action",
    "decisions",
    "decision",
    "time",
    "location",
    "summary",
    "re",
    "topic",
    "next steps",
    "duration",
    "where",
    "when",
    "to",
    "from",
    "cc",
    "http",
    "https",
];

static INLINE_TIME_FIRST: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"^\s*[\[(]?({TIME})[\])]?\s*[-–]?\s*({NAME})\s*[:\-–]\s+(.*)$"
    ))
});
static INLINE_NAME_FIRST: LazyLock<Regex> =
    LazyLock::new(|| re(&format!(r"^\s*({NAME})\s*[\[(]({TIME})[\])]\s*:?\s*(.*)$")));
static HEADER_NAME_TIME: LazyLock<Regex> =
    LazyLock::new(|| re(&format!(r"^\s*({NAME})\s+[\[(]?({TIME})[\])]?\s*$")));
static HEADER_TIME_NAME: LazyLock<Regex> =
    LazyLock::new(|| re(&format!(r"^\s*[\[(]?({TIME})[\])]?\s+({NAME})\s*$")));
static PLAIN: LazyLock<Regex> = LazyLock::new(|| re(&format!(r"^\s*({NAME})\s*:\s+(.*)$")));
static META: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)^\s*(title|meeting|subject|date)\s*:\s*(.+?)\s*$"));
static CUE_TIMING: LazyLock<Regex> = LazyLock::new(|| re(&format!(r"^\s*({TIME})\s*-->")));
static VOICE: LazyLock<Regex> = LazyLock::new(|| re(r"<v(?:\.[\w.-]+)?\s+([^>]+)>"));
static TAG: LazyLock<Regex> = LazyLock::new(|| re(r"</?[^>]{1,80}>"));
static BULLET: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*(?:[-*•‣◦]+|\d{1,3}[.)])\s+"));

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    Cues,
    Labelled,
    Notes,
    Empty,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Parsed {
    pub turns: Vec<Turn>,
    pub fmt: Format,
    pub has_speakers: bool,
    pub title: String,
    pub date: String,
}

/// How a line-based parser reads one format.
#[derive(Clone, Copy)]
enum Reader {
    Cues,
    /// An inline pattern: (time group, name group), text in group 3.
    Inline(&'static LazyLock<Regex>, usize, usize),
    /// A header line: (time group, name group); the text follows on later lines.
    Header(&'static LazyLock<Regex>, usize, usize),
    Plain,
}

pub fn parse(text: &str) -> Parsed {
    let normal = text.replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<&str> = normal.split('\n').collect();
    let (title, date) = meta(&lines);
    let body: Vec<&str> = lines.into_iter().filter(|l| !META.is_match(l)).collect();
    let mut readers = vec![
        Reader::Inline(&INLINE_TIME_FIRST, 1, 2),
        Reader::Inline(&INLINE_NAME_FIRST, 2, 1),
        Reader::Header(&HEADER_NAME_TIME, 2, 1),
        Reader::Header(&HEADER_TIME_NAME, 1, 2),
        Reader::Plain,
    ];
    if body.iter().any(|l| CUE_TIMING.is_match(l)) {
        readers.insert(0, Reader::Cues);
    }
    for reader in readers {
        let turns: Vec<Turn> = read(reader, &body)
            .into_iter()
            .filter(|t| !t.text.trim().is_empty())
            .collect();
        if turns.len() >= MIN_TURNS {
            let fmt = if matches!(reader, Reader::Cues) {
                Format::Cues
            } else {
                Format::Labelled
            };
            return Parsed {
                turns: number(merge(turns)),
                fmt,
                has_speakers: true,
                title,
                date,
            };
        }
    }
    let notes = notes(&body);
    let fmt = if notes.is_empty() {
        Format::Empty
    } else {
        Format::Notes
    };
    Parsed {
        turns: number(notes),
        fmt,
        has_speakers: false,
        title,
        date,
    }
}

fn meta(lines: &[&str]) -> (String, String) {
    let (mut title, mut date) = (String::new(), String::new());
    for line in lines.iter().filter(|l| !l.trim().is_empty()).take(6) {
        let Some(caps) = META.captures(line) else {
            continue;
        };
        let value = caps.get(2).map_or("", |m| m.as_str());
        let key = caps.get(1).map_or("", |m| m.as_str()).to_lowercase();
        if key == "date" {
            if date.is_empty() {
                date = take_chars(value, 60).to_string();
            }
        } else if title.is_empty() {
            title = take_chars(value, 80).to_string();
        }
    }
    (title, date)
}

fn is_name(name: &str) -> bool {
    !LABELS.contains(&name.trim().to_lowercase().as_str())
}

fn make_turn(speaker: &str, time: &str, text: &str) -> Turn {
    let secs = if time.is_empty() { -1 } else { to_secs(time) };
    let shown = if time.contains(['.', ',']) {
        show_time(secs)
    } else {
        time.trim().to_string()
    };
    Turn {
        id: String::new(),
        speaker: speaker.trim().to_string(),
        time: shown,
        text: collapse_ws(text),
        secs,
    }
}

/// A line without a label continues the previous turn (lines before the first turn are preamble).
fn continue_turn(turns: &mut [Turn], line: &str) {
    if let Some(last) = turns.last_mut().filter(|_| !line.trim().is_empty()) {
        last.text = format!("{} {}", last.text, collapse_ws(line))
            .trim()
            .to_string();
    }
}

fn group<'t>(caps: &regex::Captures<'t>, i: usize) -> &'t str {
    caps.get(i).map_or("", |m| m.as_str())
}

fn read(reader: Reader, lines: &[&str]) -> Vec<Turn> {
    match reader {
        Reader::Cues => cues(lines),
        Reader::Plain => plain(lines),
        Reader::Inline(pattern, t, n) => {
            let mut turns = Vec::new();
            for line in lines {
                match pattern.captures(line) {
                    Some(c) if is_name(group(&c, n)) => {
                        turns.push(make_turn(group(&c, n), group(&c, t), group(&c, 3)));
                    }
                    _ => continue_turn(&mut turns, line),
                }
            }
            turns
        }
        Reader::Header(pattern, t, n) => {
            let mut turns = Vec::new();
            for line in lines {
                match pattern.captures(line) {
                    Some(c) if is_name(group(&c, n)) => {
                        turns.push(make_turn(group(&c, n), group(&c, t), ""));
                    }
                    _ => continue_turn(&mut turns, line),
                }
            }
            turns
        }
    }
}

fn plain(lines: &[&str]) -> Vec<Turn> {
    let mut turns = Vec::new();
    for line in lines {
        match PLAIN.captures(line) {
            Some(c) if is_name(group(&c, 1)) => {
                turns.push(make_turn(group(&c, 1), "", group(&c, 2)));
            }
            _ => continue_turn(&mut turns, line),
        }
    }
    turns
}

fn cues(lines: &[&str]) -> Vec<Turn> {
    let mut turns = Vec::new();
    let (mut speaker, mut time) = (UNKNOWN.to_string(), String::new());
    let mut buffer: Vec<String> = Vec::new();
    let flush = |turns: &mut Vec<Turn>, speaker: &str, time: &str, buffer: &[String]| {
        if !time.is_empty() && !buffer.is_empty() {
            turns.push(make_turn(speaker, time, &buffer.join(" ")));
        }
    };
    for line in lines {
        let stripped = line.trim();
        if let Some(timing) = CUE_TIMING.captures(line) {
            flush(&mut turns, &speaker, &time, &buffer);
            speaker = UNKNOWN.to_string();
            time = group(&timing, 1).to_string();
            buffer.clear();
        } else if !time.is_empty()
            && !stripped.is_empty()
            && !(buffer.is_empty() && stripped.chars().all(|c| c.is_ascii_digit()))
        {
            let mut text = TAG.replace_all(line, "").trim().to_string();
            if let Some(voice) = VOICE.captures(line) {
                speaker = group(&voice, 1).trim().to_string();
            } else if let Some(p) = PLAIN
                .captures(&text)
                .filter(|p| buffer.is_empty() && is_name(group(p, 1)))
            {
                speaker = group(&p, 1).to_string();
                text = group(&p, 2).to_string();
            }
            buffer.push(text);
        } else if stripped.is_empty() {
            flush(&mut turns, &speaker, &time, &buffer);
            time.clear();
            buffer.clear();
        }
    }
    flush(&mut turns, &speaker, &time, &buffer);
    turns
}

fn notes(lines: &[&str]) -> Vec<Turn> {
    lines
        .iter()
        .map(|l| BULLET.replace(l, "").trim().to_string())
        .filter(|t| !t.is_empty())
        .map(|t| make_turn(UNKNOWN, "", &t))
        .collect()
}

fn merge(turns: Vec<Turn>) -> Vec<Turn> {
    let mut merged: Vec<Turn> = Vec::new();
    for turn in turns {
        if let Some(last) = merged.last_mut() {
            let gap = turn.secs - last.secs;
            let close = last.secs < 0 || turn.secs < 0 || (0..=MERGE_GAP_SECS).contains(&gap);
            if close && turn.speaker == last.speaker && turn.speaker != UNKNOWN {
                last.text = format!("{} {}", last.text, turn.text);
                continue;
            }
        }
        merged.push(turn);
    }
    merged
}

fn number(turns: Vec<Turn>) -> Vec<Turn> {
    turns
        .into_iter()
        .enumerate()
        .map(|(n, t)| Turn {
            id: format!("T{}", n + 1),
            ..t
        })
        .collect()
}
