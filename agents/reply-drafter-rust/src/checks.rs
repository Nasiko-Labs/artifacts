//! Pure: the faithfulness checks on a reply (grounding, the shorter alternative, quoted asks), the tone
//! checks from `tone_check`, and the code fixes applied when the one repair call did not clear them.
//! Checks never drop a reply.

use std::sync::LazyLock;

use crate::draft::{Ask, Reply};
use crate::grounding::{Ground, Span, placeholders, replace_spans, ungrounded};
use crate::style::{NONE, sign_off_of};
use crate::textstats as ts;
use crate::tone_check::{self, Target, norm};
use crate::util::{Regex, re, replace_all, split, strip_chars};

pub const ALT_MAX_RATIO: f64 = 0.6;
pub const ALT_NOT_REQUIRED_WORDS: usize = 25;
pub const NO_SHORTER: &str = "No shorter version possible; the draft is already brief.";
static PARAGRAPHS: LazyLock<Regex> = LazyLock::new(|| re(r"\n\s*\n"));
static SPACES: LazyLock<Regex> = LazyLock::new(|| re(r"\s+"));

/// What a reply is checked against.
#[derive(Debug, Clone)]
pub struct Basis {
    /// The incoming message + the user's own words.
    pub ground: Ground,
    /// The user's own words only: grounds links, emails and phone numbers.
    pub strict: Ground,
    /// The measured greeting/sign-off and the sender's name.
    pub allowed: Ground,
    pub target: Target,
    pub incoming: String,
}

pub fn spans(reply: &Reply, basis: &Basis) -> Vec<Span> {
    [
        reply.subject.as_str(),
        reply.draft.as_str(),
        reply.alt_shorter.as_deref().unwrap_or(""),
    ]
    .iter()
    .flat_map(|text| {
        ungrounded(
            text,
            &basis.ground,
            Some(&basis.allowed),
            Some(&basis.strict),
        )
    })
    .collect()
}

/// Every violation, worded for the repair call.
pub fn problems(reply: &Reply, basis: &Basis) -> Vec<String> {
    let mut found: Vec<String> = spans(reply, basis)
        .iter()
        .map(|s| {
            format!(
                "'{}' is not in the incoming message or the user's words: write {} instead",
                s.text,
                s.placeholder()
            )
        })
        .collect();
    found.extend(
        tone_check::violations(&reply.draft, &basis.target)
            .into_iter()
            .map(|v| v.message),
    );
    found.extend(alt_problems(&reply.draft, reply.alt_shorter.as_deref()));
    let mut unique: Vec<String> = Vec::new();
    for item in found {
        if !unique.contains(&item) {
            unique.push(item);
        }
    }
    unique
}

pub fn alt_limit(draft: &str) -> usize {
    (ALT_MAX_RATIO * ts::word_count(draft) as f64).floor() as usize
}

pub fn alt_required(draft: &str) -> bool {
    ts::word_count(draft) > ALT_NOT_REQUIRED_WORDS
}

pub fn alt_problems(draft: &str, alt: Option<&str>) -> Vec<String> {
    if !alt_required(draft) {
        return Vec::new();
    }
    let limit = alt_limit(draft);
    let Some(alt) = alt.filter(|a| !a.is_empty()) else {
        return vec![format!(
            "alt_shorter is missing: give the same reply in at most {limit} words"
        )];
    };
    let mut found = Vec::new();
    let words = ts::word_count(alt);
    if words > limit {
        found.push(format!(
            "alt_shorter is {words} words; keep it at most {limit} words (60% of the draft)"
        ));
    }
    let missing: Vec<String> = placeholders(draft)
        .into_iter()
        .filter(|p| !alt.contains(p.as_str()))
        .collect();
    if !missing.is_empty() {
        found.push(format!(
            "alt_shorter must keep the placeholders {}",
            missing.join(", ")
        ));
    }
    let closing = sign_off_of(draft);
    if closing != NONE && !norm(alt).ends_with(&norm(&closing)) {
        found.push(format!(
            "alt_shorter must end with the same sign-off: '{}'",
            closing.replace('\n', " / ")
        ));
    }
    found
}

/// Only asks whose quote really is in the incoming message.
pub fn kept_asks(asks: &[Ask], incoming: &str) -> Vec<Ask> {
    let source = replace_all(&SPACES, &incoming.to_lowercase(), " ").into_owned();
    asks.iter()
        .filter(|a| {
            let quote = replace_all(&SPACES, &a.quote.to_lowercase(), " ").into_owned();
            source.contains(strip_chars(&quote, " \"'“”"))
        })
        .cloned()
        .collect()
}

#[derive(Debug, Clone, PartialEq)]
pub struct Fixed {
    pub reply: Reply,
    pub notes: Vec<String>,
}

/// Code fixes for what the repair left: placeholders for made-up specifics, tone and register clean-up,
/// and the shorter alternative replaced or omitted.
pub fn fix(reply: &Reply, basis: &Basis) -> Fixed {
    let grounded = |text: &str| {
        replace_spans(
            text,
            &ungrounded(
                text,
                &basis.ground,
                Some(&basis.allowed),
                Some(&basis.strict),
            ),
        )
    };
    let draft = tone_check::fix(&grounded(&reply.draft), &basis.target);
    let alt = reply
        .alt_shorter
        .as_deref()
        .map(|a| tone_check::fix(&grounded(a), &basis.target));
    let (alt, notes) = fixed_alt(&draft, alt.as_deref());
    Fixed {
        reply: Reply {
            subject: grounded(&reply.subject),
            draft,
            alt_shorter: alt,
            asks: reply.asks.clone(),
        },
        notes,
    }
}

pub fn fixed_alt(draft: &str, alt: Option<&str>) -> (Option<String>, Vec<String>) {
    if !alt_required(draft) {
        return (None, Vec::new());
    }
    if let Some(alt) = alt.filter(|a| !a.is_empty())
        && alt_problems(draft, Some(alt)).is_empty()
    {
        return (Some(alt.to_string()), Vec::new());
    }
    let first = first_body_paragraph(draft);
    if !first.is_empty() && ts::word_count(&first) <= alt_limit(draft) {
        return (Some(first), Vec::new());
    }
    (None, vec![NO_SHORTER.to_string()])
}

/// The first paragraph that is more than a greeting line.
fn first_body_paragraph(draft: &str) -> String {
    split(&PARAGRAPHS, draft.trim())
        .into_iter()
        .find(|p| ts::word_count(p) > 3)
        .map_or(String::new(), |p| p.trim().to_string())
}
