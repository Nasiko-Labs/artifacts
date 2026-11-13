//! Pure: a canned answer for a message that is only a greeting or small talk, so it costs no LLM call.
//! Conservative on purpose: the WHOLE message must be made of the phrases below (in any order, with
//! punctuation), so anything that asks for something real goes on to the agent.

use std::sync::LazyLock;

use crate::card::SKILL_EXAMPLES;
use crate::util::{Regex, is_match, re, replace_all};

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
    r"perfect",
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
    re(&format!(r"\A(?:{alt})(?: (?:{alt}))*\z"))
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
            "I'm the reply drafter. Paste a message you received (email, Slack or Teams, or a text), tell me what you ",
            "want to say back, and paste 1-3 of your past replies. I measure your style from them and write the reply ",
            "in your voice, plus a shorter version and notes on how I matched your tone. I never invent specifics: ",
            "anything you didn't tell me becomes a placeholder like [date?]. I only draft; you copy and send it.\n\n",
            "Try:\n"
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
    let words = replace_all(&NOT_WORD, &lowered, " ");
    let collapsed = replace_all(&SPACES, &words, " ");
    let text = collapsed.trim();
    if text.is_empty() {
        return Some(INTRO.clone());
    }
    if !is_match(&ONLY_SMALL_TALK, text) {
        return None;
    }
    Some(if is_match(&THANKS_ONLY, text) {
        WELCOME.clone()
    } else {
        INTRO.clone()
    })
}
