//! Pure: who owns an action, when it is due, whether it is a follow-up, and whether it is the user's.
//!
//! Owner rules (the model never decides alone):
//! - hand-off: the cited turn asks a named attendee ("Mei, can you own the pricing doc?") and that person
//!   accepts within HANDOFF_WINDOW turns ("On it") -> owned by the accepter; no acceptance -> a follow-up
//!   with owner "Not stated".
//! - owner_ref "speaker" -> the speaker of the cited turn; "named" -> the name must be a speaker or be
//!   written in the quote, the turn or the HANDOFF_WINDOW turns around it; group words -> "Not stated".
//!
//! Due: the phrase as written, found in the quote or within HANDOFF_WINDOW turns of it, else blank.

use std::sync::LazyLock;

use regex::Regex;

use crate::attendees::is_system;
use crate::grounding as g;
use crate::items::{Kind, NOT_STATED, Turn, UNKNOWN};
use crate::util::{re, strip_chars};

pub const HANDOFF_WINDOW: usize = 3;
const GROUP_WORDS: &[&str] = &[
    "we",
    "us",
    "our",
    "team",
    "the team",
    "everyone",
    "everybody",
    "all",
    "all of us",
    "they",
    "them",
    "someone",
    "somebody",
    "anyone",
    "i",
    "me",
    "you",
    "tbd",
    "tbc",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "unassigned",
    "not stated",
    "nobody",
    "speaker",
];
static FOLLOW_UP_PHRASES: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)\b(?:revisit|circle back|check (?:in )?with|check (?:if|whether|on)|ask|follow up|look into|",
        r"think about|come back to|touch base|sync (?:up )?on|discuss)\b"
    ))
});
static DELIVERABLE_VERBS: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)\b(?:send|write|draft|update|prepare|share|create|build|fix|ship|review|schedule|",
        r"book|own|file|set up|publish|deliver|finish|complete|document|test|deploy|sign|order|",
        r"buy|present|run|migrate|implement|design|merge|launch|submit|post|email|invite|",
        r"compile|put together|hire|close|refund|approve)\b"
    ))
});
static REQUEST: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)\b(?:can|could|would|will) you\b|\bplease\b|\bare you able to\b|\bmind (?:taking|owning)\b",
    )
});
static ACCEPT: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|on it|will do|happy to|absolutely|definitely|got it|",
        r"consider it done|no problem|of course|i'?ll take (?:it|that|this)|i can|i'?ll do (?:it|that)|",
        r"i'?ll own (?:it|that)|i'?ll handle (?:it|that)|i'?m on it|count me in|can do)\b"
    ))
});

pub fn first_name(name: &str) -> String {
    name.split_whitespace().next().unwrap_or("").to_lowercase()
}

/// The speaker `name` refers to: the full name (casefold), or a first name that only one speaker has.
pub fn match_speaker(name: &str, speakers: &[String]) -> Option<String> {
    let wanted = name
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    if wanted.is_empty() {
        return None;
    }
    if let Some(s) = speakers.iter().find(|s| s.to_lowercase() == wanted) {
        return Some(s.clone());
    }
    let by_first: Vec<&String> = speakers
        .iter()
        .filter(|s| first_name(s) == wanted)
        .collect();
    (by_first.len() == 1).then(|| by_first[0].clone())
}

/// The attendee a request in this turn is addressed to, when it names exactly one other speaker.
pub fn addressee(turn: &Turn, speakers: &[String]) -> Option<String> {
    if !REQUEST.is_match(&turn.text) {
        return None;
    }
    let norm = g::normalise(&turn.text);
    let named: Vec<&String> = speakers
        .iter()
        .filter(|s| **s != turn.speaker)
        .filter(|s| g::contains_words(s, &norm) || g::contains_words(&first_name(s), &norm))
        .collect();
    (named.len() == 1).then(|| named[0].clone())
}

/// (accepted, name) for a request to a named attendee, else None.
pub fn handoff(index: usize, turns: &[Turn], speakers: &[String]) -> Option<(bool, String)> {
    let person = addressee(turns.get(index)?, speakers)?;
    let accepted = turns
        .iter()
        .skip(index + 1)
        .take(HANDOFF_WINDOW)
        .any(|later| later.speaker == person && ACCEPT.is_match(&later.text));
    Some((accepted, person))
}

fn window(index: usize, turns: &[Turn]) -> &[Turn] {
    let start = index.saturating_sub(HANDOFF_WINDOW);
    let end = (index + HANDOFF_WINDOW + 1).min(turns.len());
    turns.get(start..end).unwrap_or(&[])
}

fn nearby_text(quote: &str, index: usize, turns: &[Turn]) -> String {
    std::iter::once(quote)
        .chain(window(index, turns).iter().map(|t| t.text.as_str()))
        .collect::<Vec<_>>()
        .join(" ")
}

/// (owner, accepted): `accepted` is Some(true/false) for a hand-off, None otherwise.
pub fn resolve(
    owner_ref: Option<&str>,
    owner_name: &str,
    quote: &str,
    index: usize,
    turns: &[Turn],
    speakers: &[String],
) -> (String, Option<bool>) {
    if let Some((accepted, person)) = handoff(index, turns, speakers) {
        return if accepted {
            (person, Some(true))
        } else {
            (NOT_STATED.to_string(), Some(false))
        };
    }
    let Some(turn) = turns.get(index) else {
        return (NOT_STATED.to_string(), None);
    };
    if owner_ref == Some("speaker") {
        let unknown = turn.speaker == UNKNOWN || is_system(&turn.speaker);
        let owner = if unknown { NOT_STATED } else { &turn.speaker };
        return (owner.to_string(), None);
    }
    let lowered = owner_name.to_lowercase();
    let plain = strip_chars(&lowered, " .");
    if owner_ref != Some("named") || owner_name.is_empty() || GROUP_WORDS.contains(&plain) {
        return (NOT_STATED.to_string(), None);
    }
    if let Some(spoken) = match_speaker(owner_name, speakers) {
        return (spoken, None);
    }
    let nearby = g::normalise(&nearby_text(quote, index, turns));
    if g::contains_words(owner_name, &nearby) {
        (owner_name.to_string(), None)
    } else {
        (NOT_STATED.to_string(), None)
    }
}

pub fn grounded_due(due: &str, quote: &str, index: usize, turns: &[Turn]) -> String {
    let lowered = due.trim().to_lowercase();
    if due.is_empty() || ["null", "none", "n/a", "not stated", "-"].contains(&lowered.as_str()) {
        return String::new();
    }
    let nearby = g::normalise(&nearby_text(quote, index, turns));
    if g::contains_words(due, &nearby) {
        due.to_string()
    } else {
        String::new()
    }
}

/// The model's kind, except that an "action" worded as a check-in with no deliverable is a follow-up.
/// Code never turns a follow-up into an action.
pub fn kind_of(model_kind: Option<&str>, task: &str) -> Kind {
    if model_kind == Some("follow_up") {
        return Kind::FollowUp;
    }
    if FOLLOW_UP_PHRASES.is_match(task) && !DELIVERABLE_VERBS.is_match(task) {
        return Kind::FollowUp;
    }
    Kind::Action
}

/// None when the user has not said who they are.
pub fn is_mine(owner: &str, my_name: &str, names: &[String]) -> Option<bool> {
    if my_name.is_empty() {
        return None;
    }
    if owner.is_empty() || owner == NOT_STATED {
        return Some(false);
    }
    let mine = my_name.to_lowercase();
    if owner.to_lowercase() == mine {
        return Some(true);
    }
    if my_name.split_whitespace().count() == 1 && first_name(owner) == mine {
        let mut everyone: Vec<&str> = names.iter().map(String::as_str).collect();
        everyone.push(owner);
        everyone.sort_unstable();
        everyone.dedup();
        let same = everyone.iter().filter(|n| first_name(n) == mine).count();
        return Some(same == 1);
    }
    Some(false)
}
