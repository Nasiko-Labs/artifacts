//! Pure: a canned answer for a message that is only a greeting or small talk, so it costs no LLM call.
//! Conservative on purpose: the WHOLE message must be made of the phrases below (in any order, with
//! punctuation), so anything that asks for something real goes on to the agent.

use std::sync::LazyLock;

use regex::Regex;

use crate::card::SKILL_EXAMPLES;
use crate::util::re;

const GREETINGS: &[&str] = &[
    r"hi",
    r"hii+",
    r"hello",
    r"hey",
    r"hiya",
    r"howdy",
    r"yo",
    r"greetings",
    r"good (?:morning|afternoon|evening|day)",
    r"(?:hi|hello|hey) there",
];
const THANKS: &[&str] = &[
    r"thanks",
    r"thank you",
    r"thx",
    r"ty",
    r"cheers",
    r"thanks a lot",
    r"thank you so much",
    r"many thanks",
    r"great",
    r"cool",
    r"nice",
];
const ABOUT: &[&str] = &[
    r"who are you",
    r"what are you",
    r"what do you do",
    r"what can you do",
    r"help",
    r"how can you help(?: me)?",
    r"what is this",
    r"how does this work",
    r"how are you",
];

fn repeated(phrases: &[&str]) -> Regex {
    let alt = phrases.join("|");
    re(&format!(r"^(?:{alt})(?: (?:{alt}))*$"))
}

static ONLY_SMALL_TALK: LazyLock<Regex> = LazyLock::new(|| {
    let all: Vec<&str> = THANKS
        .iter()
        .chain(ABOUT)
        .chain(GREETINGS)
        .copied()
        .collect();
    repeated(&all)
});
static THANKS_ONLY: LazyLock<Regex> = LazyLock::new(|| repeated(THANKS));
static NOT_WORD: LazyLock<Regex> = LazyLock::new(|| re(r"[^a-z' ]+"));
static SPACES: LazyLock<Regex> = LazyLock::new(|| re(r"\s+"));

pub static INTRO: LazyLock<String> = LazyLock::new(|| {
    let examples: Vec<String> = SKILL_EXAMPLES.iter().map(|e| format!("- {e}")).collect();
    format!(
        "{}{}",
        concat!(
            "I'm the meeting notes processor. Paste a meeting transcript (Zoom, Teams, Otter style, VTT or SRT) or ",
            "your raw notes and I'll give you the attendees, topic notes, the decisions, and the action items and ",
            "follow-ups with owners and due dates. Every decision and action cites the turn and time it came from ",
            "with a quote; I never guess an owner or a date, and I send nothing. Then ask for only decisions, only ",
            "action items, only mine, show sources, as a table, as json, or a question about the meeting.\n\nTry:\n"
        ),
        examples.join("\n")
    )
});
pub static WELCOME: LazyLock<String> = LazyLock::new(|| format!("You're welcome. {}", *INTRO));
pub static OFF_TOPIC_REPLY: LazyLock<String> = LazyLock::new(|| {
    format!(
        "{}{}",
        concat!(
            "That's outside what I do, so I can't help with it here. (For a follow-up email, use a drafter ",
            "agent; for other long documents, the Summarizer.) "
        ),
        *INTRO
    )
});
pub static TOO_SHORT_REPLY: LazyLock<String> = LazyLock::new(|| {
    format!(
        "That's too short to be a meeting transcript (I need at least about 80 words). {}",
        *INTRO
    )
});
pub static NO_TEXT_YET: LazyLock<String> = LazyLock::new(|| {
    format!(
        "{}{}",
        concat!(
            "I don't see a meeting transcript in the part of our conversation I can see (I keep nothing ",
            "between messages and long earlier inputs drop out), so paste it again and ask. "
        ),
        *INTRO
    )
});

/// The canned answer, or None when the message may be a real request.
pub fn reply_to(message: &str) -> Option<String> {
    let lowered = message.to_lowercase();
    let words = NOT_WORD.replace_all(&lowered, " ");
    let text = SPACES.replace_all(&words, " ");
    let text = text.trim();
    if text.is_empty() {
        return Some(INTRO.clone());
    }
    if !ONLY_SMALL_TALK.is_match(text) {
        return None;
    }
    Some(if THANKS_ONLY.is_match(text) {
        WELCOME.clone()
    } else {
        INTRO.clone()
    })
}
