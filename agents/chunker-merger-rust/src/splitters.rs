//! Pure: where a text may be cut. Every split is a list of contiguous spans over the text itself, so the
//! parts rejoin to the input exactly; a span's `view_start` (<= start) adds the requested overlap.
//!
//! Units per strategy: speaker turns, headings (shallowest level used more than once), paragraphs, sentences.
//! A unit larger than the part size is cut at a finer boundary (paragraph, line, sentence, whitespace, then a
//! hard cut) and both parts around that cut are flagged `mid`. All offsets are character indexes.

use std::collections::{BTreeSet, HashSet};
use std::sync::LazyLock;

use regex::Regex;

use crate::textidx::Text;

pub const STRATEGIES: [&str; 4] = ["turns", "headings", "paragraphs", "sentences"];
const MIN_TURN_LINES: usize = 5;
const MIN_HEADINGS: usize = 3;
const MAX_PART_STRETCH: f64 = 1.25;
const ABBREVIATIONS: [&str; 12] = [
    "mr.", "mrs.", "ms.", "dr.", "e.g.", "i.e.", "vs.", "inc.", "no.", "st.", "jr.", "sr.",
];
const LOOKBACK_CHARS: usize = 12;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("valid regex")
}

const NAME: &str = r"[A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,3}";
static TURN: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?m)^(?:(?:\[\d{{1,2}}:\d{{2}}(?::\d{{2}})?\]\s*)?{NAME}:[ \t]|{NAME}[ \t]+\d{{1,2}}:\d{{2}}(?::\d{{2}})?[ \t]*$|\d{{1,2}}:\d{{2}}(?::\d{{2}})?[.,]\d{{3}}[ \t]*-->)"
    ))
});
static MD_HEADING: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^(#{1,6})[ \t]+\S"));
static NUM_HEADING: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?m)^(\d+(?:\.\d+)*)[.)]?[ \t]+[A-Z][^\n]{0,80}$"));
static PARAGRAPH_GAP: LazyLock<Regex> = LazyLock::new(|| re(r"\n[ \t]*\n\s*"));
static LINE_END: LazyLock<Regex> = LazyLock::new(|| re(r"\n"));
/// A sentence end and the space after it; the next character must start a sentence (checked in code).
static SENTENCE_END: LazyLock<Regex> = LazyLock::new(|| re(r#"[.!?]["')\]]*\s+"#));
static SPACE_RUN: LazyLock<Regex> = LazyLock::new(|| re(r"\s+"));

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Span {
    pub start: usize,
    pub end: usize,
    pub view_start: usize,
    pub mid: bool,
}

pub fn unit_words(strategy: &str) -> &'static str {
    match strategy {
        "turns" => "turns",
        "headings" => "sections",
        "paragraphs" => "paragraphs",
        _ => "sentences",
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Boundary {
    Paragraph,
    Line,
    Sentence,
    Space,
}

/// (start, end) byte offsets of each boundary match in `s`.
fn boundaries(s: &str, kind: Boundary) -> Vec<(usize, usize)> {
    let pattern = match kind {
        Boundary::Paragraph => &*PARAGRAPH_GAP,
        Boundary::Line => &*LINE_END,
        Boundary::Sentence => return sentence_gaps(s),
        Boundary::Space => &*SPACE_RUN,
    };
    pattern.find_iter(s).map(|m| (m.start(), m.end())).collect()
}

/// `[.!?]["')\]]*\s+` followed by an optional opening quote/bracket and an ASCII capital or digit.
fn sentence_gaps(s: &str) -> Vec<(usize, usize)> {
    let starts_sentence = |rest: &str| {
        let mut chars = rest.chars();
        let begins = |c: char| c.is_ascii_uppercase() || c.is_ascii_digit();
        match chars.next() {
            Some(c) if "\"'([".contains(c) => chars.next().is_some_and(begins),
            Some(c) => begins(c),
            None => false,
        }
    };
    SENTENCE_END
        .find_iter(s)
        .filter(|m| starts_sentence(&s[m.end()..]))
        .map(|m| (m.start(), m.end()))
        .collect()
}

pub fn turn_starts(text: &Text) -> Vec<usize> {
    TURN.find_iter(text.s)
        .map(|m| text.char_at(m.start()))
        .collect()
}

pub fn heading_starts(text: &Text) -> Vec<usize> {
    let mut found: Vec<(usize, usize)> = MD_HEADING
        .captures_iter(text.s)
        .map(|c| (text.char_at(c.get(0).map_or(0, |m| m.start())), c[1].len()))
        .collect();
    if found.len() < MIN_HEADINGS {
        found = NUM_HEADING
            .captures_iter(text.s)
            .map(|c| {
                (
                    text.char_at(c.get(0).map_or(0, |m| m.start())),
                    c[1].matches('.').count() + 1,
                )
            })
            .collect();
    }
    let levels: BTreeSet<usize> = found.iter().map(|(_, level)| *level).collect();
    let Some(&shallowest) = levels.first() else {
        return Vec::new();
    };
    // The shallowest level used more than once: a lone document title does not set the level.
    let top = levels
        .iter()
        .copied()
        .find(|lv| found.iter().filter(|(_, x)| x == lv).count() > 1)
        .unwrap_or(shallowest);
    found
        .into_iter()
        .filter(|(_, level)| *level == top)
        .map(|(at, _)| at)
        .collect()
}

pub fn paragraph_starts(text: &Text) -> Vec<usize> {
    boundaries(text.s, Boundary::Paragraph)
        .into_iter()
        .map(|(_, end)| text.char_at(end))
        .filter(|&end| end < text.len())
        .collect()
}

pub fn sentence_starts(text: &Text) -> Vec<usize> {
    let mut starts = Vec::new();
    for (start_byte, end_byte) in sentence_gaps(text.s) {
        let at = text.char_at(start_byte);
        let window = text.slice(at.saturating_sub(LOOKBACK_CHARS), at + 1);
        let token = window
            .rsplit(char::is_whitespace)
            .next()
            .unwrap_or_default();
        if !token.is_empty() && ABBREVIATIONS.contains(&token.to_lowercase().as_str()) {
            continue;
        }
        let end = text.char_at(end_byte);
        if end < text.len() {
            starts.push(end);
        }
    }
    starts
}

pub fn finder(strategy: &str, text: &Text) -> Vec<usize> {
    match strategy {
        "turns" => turn_starts(text),
        "headings" => heading_starts(text),
        "paragraphs" => paragraph_starts(text),
        _ => sentence_starts(text),
    }
}

/// Sorted unit start offsets, always beginning with 0.
pub fn unit_starts(text: &Text, strategy: &str) -> Vec<usize> {
    let set: BTreeSet<usize> = std::iter::once(0).chain(finder(strategy, text)).collect();
    set.into_iter().collect()
}

pub fn detect(text: &Text) -> &'static str {
    if turn_starts(text).len() >= MIN_TURN_LINES {
        return "turns";
    }
    if heading_starts(text).len() >= MIN_HEADINGS {
        return "headings";
    }
    if paragraph_starts(text).is_empty() {
        "sentences"
    } else {
        "paragraphs"
    }
}

/// A cut in (lo, hi], preferring the latest paragraph, line, sentence, then whitespace boundary in the upper
/// half of the window; a hard cut at `hi` when there is none.
pub fn finer_cut(text: &Text, lo: usize, hi: usize) -> usize {
    let floor = lo + std::cmp::max(1, hi.saturating_sub(lo) / 2);
    if floor >= hi {
        return hi;
    }
    let window = text.slice(floor, hi);
    let window_text = Text::new(window);
    for kind in [
        Boundary::Paragraph,
        Boundary::Line,
        Boundary::Sentence,
        Boundary::Space,
    ] {
        let last = boundaries(window, kind)
            .into_iter()
            .map(|(_, end)| floor + window_text.char_at(end))
            .rfind(|&e| lo < e && e <= hi);
        if let Some(cut) = last {
            return cut;
        }
    }
    hi
}

/// (cut offsets, non-structural cuts): whole units packed while the part stays within `size`.
pub fn pack_by_size(text: &Text, starts: &[usize], size: usize) -> (Vec<usize>, HashSet<usize>) {
    let (mut cuts, mut mids, mut pos) = (Vec::new(), HashSet::new(), 0);
    let size = size.max(1);
    while text.len() - pos > size {
        let hi = pos + size;
        let best = starts[starts.partition_point(|&s| s <= hi).saturating_sub(1)];
        let cut = if best > pos {
            best
        } else {
            let cut = finer_cut(text, pos, hi);
            mids.insert(cut);
            cut
        };
        cuts.push(cut);
        pos = cut;
    }
    (cuts, mids)
}

/// Python's `round()`: halves go to the even neighbour.
pub fn round_half_even(x: f64) -> i64 {
    let r = x.round();
    if (x - x.trunc()).abs() == 0.5 && (r as i64) % 2 != 0 {
        (r - x.signum()) as i64
    } else {
        r as i64
    }
}

/// Cuts for `parts` even parts: each at the structural break nearest the ideal offset, never letting a part
/// grow beyond 125% of the even size.
pub fn pack_into(text: &Text, starts: &[usize], parts: usize) -> (Vec<usize>, HashSet<usize>) {
    let length = text.len();
    let parts = parts.max(1);
    let cap = ((length as f64 / parts as f64).ceil() * MAX_PART_STRETCH).floor() as usize;
    let (mut cuts, mut mids, mut pos) = (Vec::new(), HashSet::new(), 0usize);
    for k in 1..parts {
        let ideal = round_half_even(k as f64 * length as f64 / parts as f64).max(0) as usize;
        let (lo, hi) = (pos + 1, std::cmp::min(pos + cap, length.saturating_sub(1)));
        if lo > hi {
            break;
        }
        let idx = starts.partition_point(|&s| s < ideal);
        let near = starts[idx.saturating_sub(1)..std::cmp::min(idx + 1, starts.len())]
            .iter()
            .copied()
            .filter(|&s| lo <= s && s <= hi)
            .min_by_key(|&s| (s.abs_diff(ideal), s));
        let cut = match near {
            Some(cut) => cut,
            None => {
                let cut = finer_cut(text, pos, ideal.clamp(lo, hi));
                mids.insert(cut);
                cut
            }
        };
        cuts.push(cut);
        pos = cut;
    }
    (cuts, mids)
}

pub fn to_spans(length: usize, cuts: &[usize], mids: &HashSet<usize>) -> Vec<Span> {
    let edges: Vec<usize> = std::iter::once(0)
        .chain(cuts.iter().copied())
        .chain(std::iter::once(length))
        .collect();
    edges
        .windows(2)
        .filter(|w| w[1] > w[0])
        .map(|w| Span {
            start: w[0],
            end: w[1],
            view_start: w[0],
            mid: mids.contains(&w[0]) || mids.contains(&w[1]),
        })
        .collect()
}

/// Each part after the first also shows the end of the previous part: `units` whole units back (when it
/// starts on a unit), else `chars` characters back, moved forward to a word start.
pub fn with_overlap(
    text: &Text,
    spans: &[Span],
    starts: &[usize],
    units: usize,
    chars: usize,
) -> Vec<Span> {
    let mut out: Vec<Span> = spans.iter().take(1).copied().collect();
    for pair in spans.windows(2) {
        let (prev, span) = (pair[0], pair[1]);
        let mut view = span.start;
        let unit_index = starts.iter().position(|&s| s == span.start);
        if let (true, Some(index)) = (units > 0, unit_index) {
            view = starts[index.saturating_sub(units)];
        } else if chars > 0 {
            view = span.start.saturating_sub(chars);
            let gap = SPACE_RUN.find(text.slice(view, span.start));
            if let Some(gap) = gap.filter(|_| view > 0) {
                view += text.slice(view, span.start)[..gap.end()].chars().count();
            }
        }
        out.push(Span {
            view_start: view.max(prev.start),
            ..span
        });
    }
    out
}

pub fn unit_label(starts: &[usize], strategy: &str, span: &Span) -> String {
    let first = starts.partition_point(|&s| s <= span.start);
    let last = starts.partition_point(|&s| s < span.end);
    let words = unit_words(strategy);
    if last > first {
        format!("{words} {first}-{last}")
    } else {
        format!("{} {first}", &words[..words.len() - 1])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vtt_cues_and_bracketed_times_count_as_turns() {
        let text: Vec<String> = (0..6)
            .map(|i| format!("00:0{i}:00.000 --> 00:0{i}:05.000\nSpeaker line {i}."))
            .collect();
        assert_eq!(turn_starts(&Text::new(&text.join("\n"))).len(), 6);
        assert_eq!(
            turn_starts(&Text::new("[10:02] Priya: hi\n[10:03] Sam: hello")).len(),
            2
        );
    }

    #[test]
    fn sentence_starts_skip_abbreviations() {
        let s = "Dr. Smith came. We met Mr. Lee, e.g. at noon. Then we left! Fine.";
        let text = Text::new(s);
        let found: Vec<&str> = sentence_starts(&text)
            .into_iter()
            .map(|i| text.slice(i, i + 4))
            .collect();
        assert_eq!(found, ["We m", "Then", "Fine"]);
    }

    #[test]
    fn python_rounding() {
        assert_eq!(
            (
                round_half_even(2.5),
                round_half_even(3.5),
                round_half_even(2.4),
                round_half_even(2.6)
            ),
            (2, 4, 2, 3)
        );
    }

    #[test]
    fn labels() {
        let starts = [0, 10, 20, 30];
        assert_eq!(
            unit_label(
                &starts,
                "turns",
                &Span {
                    start: 10,
                    end: 30,
                    view_start: 10,
                    mid: false
                }
            ),
            "turns 2-3"
        );
        assert_eq!(
            unit_label(
                &starts,
                "headings",
                &Span {
                    start: 20,
                    end: 25,
                    view_start: 20,
                    mid: false
                }
            ),
            "section 3"
        );
    }
}
