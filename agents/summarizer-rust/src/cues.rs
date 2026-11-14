//! Pure: an explicit ask to summarize, in the user's own words, split from the text it is about.
//!
//! ```text
//! "summarize this: <text>", "can you summarize this for me\n<text>", "tl;dr <text>",
//! "give me the gist of this: <text>", "what are the action items here? <text>", "<text>\n\nsummarize"
//! ```
//!
//! The cue may open the message (with "please", "can you", "hey" before it and "this", "for me", "the
//! following" after it) or be its own last line. A cue followed by more instruction ("summarize this
//! call, focus on pricing: <text>") keeps that instruction. Kept in step with the Python variants' copies.

use std::sync::LazyLock;

use regex::Regex;

use crate::util::{char_len, re};

pub const INSTRUCTION_MAX_CHARS: usize = 300;
/// An explicit cue summarizes text this long, not only >= `MIN_SOURCE_WORDS`.
pub const CUED_MIN_SOURCE_WORDS: usize = 15;
/// Words after the cue that can still be part of the instruction.
const CUE_INSTRUCTION_MAX_WORDS: usize = 20;

const CUE_LEAD: &str = concat!(
    r"(?:(?:hey|hi|ok|okay|so)[,!]?\s+)?(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?",
    r"(?:just\s+)?"
);
const CUE_WORDS: &str = concat!(
    r"(?:summari[sz]e|sum\s+(?:it\s+|this\s+|that\s+)?up|tl\s*;?\s*dr|recap|brief\s+me(?:\s+on)?",
    r"|(?:give\s+me\s+|what(?:'s|\s+is)\s+)?the\s+gist",
    r"|(?:give\s+me\s+|list\s+|what\s+are\s+)?(?:the\s+)?(?:key|main)\s+(?:points|takeaways)",
    r"|(?:give\s+me\s+|write\s+|make\s+)?an?\s+(?:short\s+|quick\s+|brief\s+)?(?:summary|recap|tl\s*;?\s*dr)",
    r"|what\s+are\s+the\s+(?:action\s+items|decisions|takeaways|next\s+steps)",
    r"|what\s+did\s+(?:they|we)\s+decide)"
);
const CUE_FILLER: &str = concat!(
    r"(?:\s+(?:(?:of|on|for|in|from)\s+)?(?:this|that|these|the\s+following|the\s+text\s+below|below|here))?",
    r"(?:\s+for\s+me)?(?:\s+(?:here|below))?(?:\s+please)?"
);
const CUE_END: &str = r"[ \t]*(?:[:?!.]+|[-\x{2013}\x{2014}]+)?";
const INSTRUCTION_TRIM: &[char] = &[' ', '\t', ':', '-', '\u{2013}', '\u{2014}'];

static LEADING_CUE: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)^\s*{CUE_LEAD}(?P<cue>{CUE_WORDS}){CUE_FILLER}(?P<end>{CUE_END})(?:\s|$)"
    ))
});
static CUE_LINE: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)^{CUE_LEAD}{CUE_WORDS}{CUE_FILLER}{CUE_END}$"
    ))
});
static LINE_COLON: LazyLock<Regex> = LazyLock::new(|| re(r":(?:[ \t]|$)"));
static SENTENCE_END: LazyLock<Regex> = LazyLock::new(|| re(r#"[.!?]["')\]]*\s+\S"#));
static SPEAKER: LazyLock<Regex> = LazyLock::new(|| re(r"^[A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,2}$"));

/// (instruction, source) when the message asks to summarize in words ("summarize this: ...",
/// "tl;dr ...", "what are the action items here? ...", or "...\nsummarize" after the text), else None.
/// None too when the cue has no text with it ("summarize this"), so that case keeps its own route.
pub fn find_cue(text: &str) -> Option<(String, String)> {
    let stripped = text.trim();
    leading_cue(stripped).or_else(|| trailing_cue(stripped))
}

fn leading_cue(text: &str) -> Option<(String, String)> {
    let caps = LEADING_CUE.captures(text)?;
    let end = caps.name("end")?;
    let cut = end.end();
    let whole = caps.get(0)?.as_str();
    if !end.as_str().trim().is_empty() || whole.ends_with('\n') {
        return cut_pair(text[..cut].trim_end_matches(INSTRUCTION_TRIM), &text[cut..]);
    }
    let first_line = text.split('\n').next().unwrap_or("");
    let colon = (cut <= first_line.len())
        .then(|| LINE_COLON.find_at(first_line, cut))
        .flatten();
    if let Some(colon) = colon {
        let extra = &text[cut..colon.start()];
        if instruction_like(extra) && !SPEAKER.is_match(extra.trim()) {
            return cut_pair(&text[..colon.start()], &text[colon.end()..]);
        }
    }
    if text.contains('\n') && cut <= first_line.len() && instruction_like(&first_line[cut..]) {
        return cut_pair(first_line, &text[first_line.len()..]);
    }
    let rest = text[cut..].trim();
    let asks = caps
        .name("cue")
        .is_some_and(|c| c.as_str().to_lowercase().starts_with("what"));
    if asks || words(rest) < CUED_MIN_SOURCE_WORDS || !SENTENCE_END.is_match(rest) {
        return None;
    }
    cut_pair(&text[..cut], rest)
}

fn trailing_cue(text: &str) -> Option<(String, String)> {
    let (head, last) = text.rsplit_once('\n')?;
    if !CUE_LINE.is_match(last.trim()) {
        return None;
    }
    cut_pair(last, head)
}

/// Words between the cue and its separator that read as more instruction ("this call, focus on
/// pricing"), not as the start of the text ("tl;dr We met on Monday. ...", "tl;dr Priya: we ...").
fn instruction_like(extra: &str) -> bool {
    let extra = extra.trim();
    if char_len(extra) > INSTRUCTION_MAX_CHARS || words(extra) > CUE_INSTRUCTION_MAX_WORDS {
        return false;
    }
    !(SENTENCE_END.is_match(extra) || extra.contains('?') || LINE_COLON.is_match(extra))
}

fn cut_pair(instruction: &str, source: &str) -> Option<(String, String)> {
    let (instruction, source) = (instruction.trim(), source.trim());
    (!instruction.is_empty() && !source.is_empty())
        .then(|| (instruction.to_string(), source.to_string()))
}

fn words(text: &str) -> usize {
    text.split_whitespace().count()
}
