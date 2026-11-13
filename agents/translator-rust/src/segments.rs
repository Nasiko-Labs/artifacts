//! Pure: the masked text split into segments (paragraphs on blank lines; list items and table rows stay
//! with their paragraph; a paragraph over SEGMENT_MAX_CHARS splits at sentence ends), and segments packed
//! into chunks of at most CHUNK_CHARS for one model call each. Sizes are in chars.

use std::sync::LazyLock;

use regex::Regex;

use crate::util::{char_len, lines, re};

pub const SEGMENT_MAX_CHARS: usize = 3_000;
pub const CHUNK_CHARS: usize = 3_000;
pub const MAX_CHUNKS: usize = 20;

static PARAGRAPH_BREAK: LazyLock<Regex> = LazyLock::new(|| re(r"\n[ \t]*\n\s*"));
static MARKER: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(\s*(?:[-*+•]|\d{1,3}[.)])\s+|\s*#{1,6}\s+|\s*>\s?)"));
static LIST: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+"));
static HEADING: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*#{1,6}\s+"));
static TABLE: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*\|"));
const SENTENCE_ENDS: &[char] = &['.', '!', '?', '。', '！', '？'];

/// One unit the model translates. `sep` is the whitespace that followed it in the source; `field` names
/// the envelope field it came from (None for plain text).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Segment {
    pub text: String,
    pub sep: String,
    pub field: Option<String>,
}

/// Segments of `text`; leading whitespace is dropped, trailing whitespace is kept as the last sep.
pub fn split(text: &str, field: Option<&str>) -> Vec<Segment> {
    let body = text.trim_start();
    let mut pieces: Vec<(&str, &str)> = Vec::new();
    let mut last = 0;
    for m in PARAGRAPH_BREAK.find_iter(body) {
        pieces.push((&body[last..m.start()], m.as_str()));
        last = m.end();
    }
    pieces.push((&body[last..], ""));
    let mut segments: Vec<Segment> = Vec::new();
    for (paragraph, sep) in pieces {
        let stripped = paragraph.trim_end();
        let sep = format!("{}{sep}", &paragraph[stripped.len()..]);
        if stripped.trim().is_empty() {
            if let Some(last) = segments.last_mut() {
                last.sep = format!("{}{paragraph}{sep}", last.sep);
            }
            continue;
        }
        segments.extend(sentences(stripped, &sep, field));
    }
    segments
}

/// `(?<=[.!?。！？])\s+` split: the pieces, without the whitespace that ended each one.
fn sentence_pieces(paragraph: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    let mut chars = paragraph.chars().peekable();
    while let Some(c) = chars.next() {
        let ends = current
            .chars()
            .last()
            .is_some_and(|p| SENTENCE_ENDS.contains(&p));
        if c.is_whitespace() && ends {
            while chars.peek().is_some_and(|n| n.is_whitespace()) {
                chars.next();
            }
            out.push(std::mem::take(&mut current));
            continue;
        }
        current.push(c);
    }
    out.push(current);
    out
}

fn sentences(paragraph: &str, sep: &str, field: Option<&str>) -> Vec<Segment> {
    let segment = |text: String, sep: &str| Segment {
        text,
        sep: sep.to_string(),
        field: field.map(str::to_string),
    };
    if char_len(paragraph) <= SEGMENT_MAX_CHARS {
        return vec![segment(paragraph.to_string(), sep)];
    }
    let mut out = Vec::new();
    let mut current = String::new();
    for sentence in sentence_pieces(paragraph) {
        if !current.is_empty() && char_len(&current) + 1 + char_len(&sentence) > SEGMENT_MAX_CHARS {
            out.push(segment(std::mem::take(&mut current), " "));
            current = sentence;
        } else if current.is_empty() {
            current = sentence;
        } else {
            current = format!("{current} {sentence}");
        }
    }
    out.push(segment(current, sep));
    out
}

/// Segment indexes per chunk; the size grows only if the text would need more than MAX_CHUNKS.
pub fn chunk(segments: &[Segment]) -> Vec<Vec<usize>> {
    let mut size = CHUNK_CHARS;
    loop {
        let mut chunks: Vec<Vec<usize>> = Vec::new();
        let mut used = 0;
        for (i, seg) in segments.iter().enumerate() {
            let len = char_len(&seg.text);
            match chunks.last_mut() {
                Some(last) if used + len <= size => {
                    last.push(i);
                    used += len;
                }
                _ => {
                    chunks.push(vec![i]);
                    used = len;
                }
            }
        }
        if chunks.len() <= MAX_CHUNKS {
            return chunks;
        }
        size += size / 4;
    }
}

pub fn join(texts: &[String], segments: &[&Segment]) -> String {
    texts
        .iter()
        .zip(segments)
        .map(|(t, s)| format!("{t}{}", s.sep))
        .collect::<String>()
        .trim_end()
        .to_string()
}

/// (list items, headings, table rows) of a segment.
pub fn shape(text: &str) -> (usize, usize, usize) {
    let all = lines(text);
    let count = |rx: &Regex| all.iter().filter(|l| rx.is_match(l)).count();
    (count(&LIST), count(&HEADING), count(&TABLE))
}

/// Puts back list/heading/quote markers the model dropped, line by line, when the line counts match.
/// Returns (text, changed).
pub fn restore_markers(source: &str, translated: &str) -> (String, bool) {
    let (src_lines, out_lines) = (lines(source), lines(translated));
    if src_lines.len() != out_lines.len() {
        return (translated.to_string(), false);
    }
    let mut changed = false;
    let fixed: Vec<String> = src_lines
        .iter()
        .zip(&out_lines)
        .map(|(src, out)| match MARKER.captures(src) {
            Some(marker) if !MARKER.is_match(out) => {
                changed = true;
                format!(
                    "{}{}",
                    marker.get(1).map_or("", |m| m.as_str()),
                    out.trim_start()
                )
            }
            _ => out.to_string(),
        })
        .collect();
    (fixed.join("\n"), changed)
}
