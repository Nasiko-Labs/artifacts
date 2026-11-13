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
            "I explain error messages and stack traces: the probable cause, the line to look at, a fix and how to confirm ",
            "it. Paste the error (Python, JavaScript/Node, Java/Kotlin, Go, Rust, Ruby or C#) and, if you can, the code ",
            "around the failing line under a header like `# file: app/orders.py`. I parse the trace in code, check every ",
            "quote and line number against your paste, never run anything and send nothing.\n\nTry:\n"
        ),
        examples.join("\n")
    )
});
pub static WELCOME: LazyLock<String> = LazyLock::new(|| format!("You're welcome. {}", *INTRO));
pub static OFF_TOPIC_REPLY: LazyLock<String> = LazyLock::new(|| {
    format!(
        "That's outside what I do, so I can't help with it here. {}",
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
