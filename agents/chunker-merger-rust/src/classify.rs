//! Pure: what this message asks for, decided in code. A request's cues are read from its first line (the
//! "head"); everything after it is the payload, so words inside a pasted text never change the route.

use std::sync::LazyLock;

use regex::Regex;

use crate::smalltalk;
use crate::split::SplitOptions;

const HEAD_MAX_CHARS: usize = 400;
const STEP_MAX_CHARS: usize = 500;
const THOUSAND: f64 = 1000.0;
const CHARS_PER_TOKEN: f64 = 4.0;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("valid regex")
}

static SPLIT: LazyLock<Regex> =
    LazyLock::new(|| re(r"\b(?:split|chunk|cut|divide|break (?:\w+ )?(?:in)?to)\b"));
static MERGE: LazyLock<Regex> = LazyLock::new(|| re(r"\b(?:merge|combine|join)\b"));
static PART_OF: LazyLock<Regex> = LazyLock::new(|| re(r"\bpart (\d+) of (\d+)\b"));
static APPEND: LazyLock<Regex> =
    LazyLock::new(|| re(r"\b(?:append|more coming|more to come|to be continued|there'?s more)\b"));
static END: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"^(?:end|that'?s all|that is all|that'?s it|done|that'?s everything|finished|no more)\b|\blast (?:part|piece)\b",
    )
});
static SHOW: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^(?:show|print|give|display|paste)(?: me)?(?: the)? (?:part|chunk|piece) (\d+)\b")
});
static LIST: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^(?:list|show)(?: me)?(?: the| all(?: the)?)? (?:parts|chunks|pieces)\b")
});
static AS_JSON: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(?:as|in|show(?: it)? as|give(?: me)? the) json\b"));
static RESPLIT: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(?:split|cut|chunk)(?: it| this)? again\b|^re-?split\b"));
static MERGE_MORE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^(?:merge|combine|add)(?: it)? (?:with|to) (?:this|these)\b|^add (?:this|these)\b")
});
static MERGE_NOTES: LazyLock<Regex> = LazyLock::new(|| re(r"\bmerge notes\b"));
static PARTS: LazyLock<Regex> =
    LazyLock::new(|| re(r"\b(\d{1,3})\s+(?:equal\s+)?(?:parts|pieces|chunks)\b"));
static CHARS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:chars?|characters?)\b"));
static TOKENS: LazyLock<Regex> = LazyLock::new(|| re(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*tokens?\b"));
static OVERLAP: LazyLock<Regex> =
    LazyLock::new(|| re(r"\boverlap(?:ping)?(?: of| by)?\s+(\d[\d,]*)\s*([a-z]+)"));
static STEP: LazyLock<Regex> =
    LazyLock::new(|| re(r"\bfor (?:each|every) (?:part|chunk|piece)\s*[:,\-]?\s*(.+)$"));
static STRATEGY: LazyLock<[(&str, Regex); 4]> = LazyLock::new(|| {
    [
        ("turns", re(r"\bby (?:speaker )?(?:turns?|speakers?)\b")),
        ("headings", re(r"\bby (?:headings?|sections?)\b")),
        ("paragraphs", re(r"\bby paragraphs?\b")),
        ("sentences", re(r"\bby sentences?\b")),
    ]
});

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Intro,
    Decline,
    Split,
    Append,
    End,
    Merge,
    ShowPart,
    ListParts,
    AsJson,
    Resplit,
    MergeMore,
    MergeNotes,
}

impl Kind {
    pub fn is_follow_up(self) -> bool {
        matches!(
            self,
            Kind::ShowPart
                | Kind::ListParts
                | Kind::AsJson
                | Kind::Resplit
                | Kind::MergeMore
                | Kind::MergeNotes
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub kind: Kind,
    pub payload: String,
    pub opts: SplitOptions,
    pub number: u64,
    pub reply: String,
}

impl Request {
    pub fn new(kind: Kind) -> Self {
        Self {
            kind,
            payload: String::new(),
            opts: SplitOptions::default(),
            number: 0,
            reply: String::new(),
        }
    }

    pub fn with_payload(kind: Kind, payload: &str) -> Self {
        Self {
            payload: payload.to_owned(),
            ..Self::new(kind)
        }
    }
}

/// Digits as a number, saturating (a 40-digit "part" number is just "too big").
fn number(digits: &str) -> u64 {
    digits.parse().unwrap_or(u64::MAX)
}

pub fn classify(text: &str) -> Request {
    if let Some(canned) = smalltalk::reply_to(text) {
        return Request {
            reply: canned,
            ..Request::new(Kind::Intro)
        };
    }
    let (head, payload) = head_and_payload(text);
    let low = head
        .to_lowercase()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    let opts = parse_options(&head);
    if let Some(found) = SHOW.captures(&low) {
        return Request {
            number: number(&found[1]),
            ..Request::new(Kind::ShowPart)
        };
    }
    for (kind, pattern) in [
        (Kind::ListParts, &*LIST),
        (Kind::AsJson, &*AS_JSON),
        (Kind::Resplit, &*RESPLIT),
    ] {
        if pattern.is_match(&low) {
            return Request {
                opts,
                ..Request::new(kind)
            };
        }
    }
    if MERGE_NOTES.is_match(&low) {
        return Request::new(Kind::MergeNotes);
    }
    if MERGE_MORE.is_match(&low) {
        return Request::with_payload(Kind::MergeMore, text);
    }
    let (merge_cue, split_cue) = (MERGE.find(&low), SPLIT.find(&low));
    if let Some(merge_at) = merge_cue
        && split_cue.is_none_or(|s| merge_at.start() < s.start())
    {
        return Request::with_payload(Kind::Merge, text);
    }
    let part_of = PART_OF.captures(&low);
    let last_piece = part_of
        .as_ref()
        .is_some_and(|c| number(&c[1]) >= number(&c[2]));
    if last_piece || END.is_match(&low) {
        return Request {
            opts,
            ..Request::with_payload(Kind::End, &payload)
        };
    }
    if part_of.is_some() || APPEND.is_match(&low) {
        return Request {
            opts,
            ..Request::with_payload(Kind::Append, &payload)
        };
    }
    if split_cue.is_some() {
        return Request {
            opts,
            ..Request::with_payload(Kind::Split, &payload)
        };
    }
    Request::new(Kind::Decline)
}

/// (the request line, the text after it). A multi-line message's first line is the head when it is short;
/// a one-line message is cut after its last ': ' within the first HEAD_MAX_CHARS.
pub fn head_and_payload(text: &str) -> (String, String) {
    let stripped = text.trim();
    if let Some((first, rest)) = stripped.split_once('\n')
        && first.chars().count() <= HEAD_MAX_CHARS
    {
        return (
            first.trim().to_owned(),
            rest.trim_matches('\n').trim().to_owned(),
        );
    }
    let window: String = stripped.chars().take(HEAD_MAX_CHARS).collect();
    match window.rfind(": ") {
        Some(cut) => (
            stripped[..cut + 1].to_owned(),
            stripped[cut + 2..].trim().to_owned(),
        ),
        None => {
            let rest: String = stripped.chars().skip(HEAD_MAX_CHARS).collect();
            (window, rest.trim().to_owned())
        }
    }
}

pub fn parse_options(head: &str) -> SplitOptions {
    let mut low = head
        .to_lowercase()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    let (mut overlap_units, mut overlap_chars) = (0, 0);
    if let Some(found) = OVERLAP.captures(&low) {
        let amount = parse_number(&found[1]) as usize;
        if found[2].starts_with("char") {
            overlap_chars = amount;
        }
        overlap_units = if overlap_chars > 0 { 0 } else { amount };
        let whole = found.get(0).map_or(0..0, |m| m.range());
        low = format!("{}{}", &low[..whole.start], &low[whole.end..]);
    }
    let spaced = head.split_whitespace().collect::<Vec<_>>().join(" ");
    let step = STEP
        .captures(&spaced)
        .map(|c| {
            c[1].trim()
                .trim_end_matches(':')
                .trim()
                .chars()
                .take(STEP_MAX_CHARS)
                .collect()
        })
        .unwrap_or_default();
    let scaled = |c: &regex::Captures, factor: f64| {
        (parse_number(&c[1]) * if c.get(2).is_some() { THOUSAND } else { 1.0 } * factor) as usize
    };
    let size = CHARS
        .captures(&low)
        .map(|c| scaled(&c, 1.0))
        .or_else(|| TOKENS.captures(&low).map(|c| scaled(&c, CHARS_PER_TOKEN)))
        .filter(|&s| s > 0);
    let parts = PARTS
        .captures(&low)
        .map(|c| number(&c[1]) as usize)
        .filter(|&p| p > 0);
    let strategy = STRATEGY
        .iter()
        .find(|(_, p)| p.is_match(&low))
        .map(|(name, _)| (*name).to_owned());
    SplitOptions {
        size,
        parts: if size.is_some() { None } else { parts },
        strategy,
        overlap_units,
        overlap_chars,
        step,
    }
}

/// "8,000" -> 8000.0; "1.5" -> 1.5.
fn parse_number(raw: &str) -> f64 {
    raw.replace(',', "").parse().unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn routes() {
        let cases = [
            ("hi", Kind::Intro),
            ("what can you do?", Kind::Intro),
            ("What's 2+2?", Kind::Decline),
            ("summarise this\nPriya: hello there", Kind::Decline),
            ("Split into 4 parts\nPriya: hi", Kind::Split),
            ("chunk this by headings\n# A", Kind::Split),
            ("break it into 3 parts\ntext", Kind::Split),
            ("Merge these results\n```json\n{}\n```", Kind::Merge),
            ("combine: {} {}", Kind::Merge),
            ("part 1 of 3, more coming\ntext", Kind::Append),
            ("append\ntext", Kind::Append),
            ("part 3 of 3\ntext", Kind::End),
            ("that's all", Kind::End),
            ("show part 3", Kind::ShowPart),
            ("list the parts", Kind::ListParts),
            ("as json", Kind::AsJson),
            ("split again with 10,000 chars", Kind::Resplit),
            ("merge with this one\n{}", Kind::MergeMore),
            ("show the merge notes", Kind::MergeNotes),
        ];
        for (text, kind) in cases {
            assert_eq!(classify(text).kind, kind, "{text}");
        }
    }

    #[test]
    fn cue_words_inside_the_pasted_text_do_not_route() {
        assert_eq!(
            classify("Split into 2 parts\nSam: let's merge the teams").kind,
            Kind::Split
        );
        assert_eq!(
            classify("What's the weather?\nplease split the bill").kind,
            Kind::Decline
        );
    }

    #[test]
    fn head_and_payload_split() {
        assert_eq!(
            head_and_payload("Split into 2 parts:\nline one\nline two"),
            ("Split into 2 parts:".into(), "line one\nline two".into())
        );
        assert_eq!(
            head_and_payload("split this into 2 parts: the text here"),
            ("split this into 2 parts:".into(), "the text here".into())
        );
    }

    #[test]
    fn options() {
        let o = |head: &str| parse_options(head);
        assert_eq!(o("split into parts of 8,000 chars").size, Some(8000));
        assert_eq!(o("split into 4 parts").parts, Some(4));
        assert_eq!(o("split ~2k tokens each").size, Some(8000));
        let turns = o("split by turns, overlap 2 turns");
        assert_eq!(
            (turns.strategy.as_deref(), turns.overlap_units),
            (Some("turns"), 2)
        );
        let paras = o("split into 3 parts by paragraphs, overlap 400 chars");
        assert_eq!(
            (paras.parts, paras.strategy.as_deref(), paras.overlap_chars),
            (Some(3), Some("paragraphs"), 400)
        );
        let step = o("Split into 4 parts by turns, for each part: extract action items:");
        assert_eq!(
            (step.parts, step.strategy.as_deref(), step.step.as_str()),
            (Some(4), Some("turns"), "extract action items")
        );
        assert_eq!(o("split by headings").strategy.as_deref(), Some("headings"));
    }
}
