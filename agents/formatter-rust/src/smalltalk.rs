//! Pure: a canned answer for a message that is only a greeting or small talk, so it costs nothing.
//! Conservative on purpose: the WHOLE message must be made of the phrases below (in any order, with
//! punctuation), so anything that asks for something real goes on to the agent.

use std::sync::LazyLock;

use regex::Regex;

use crate::card::SKILL_EXAMPLES;

const GREETINGS: [&str; 10] = [
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
const THANKS: [&str; 11] = [
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
const ABOUT: [&str; 9] = [
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
    Regex::new(&format!("^(?:{alt})(?: (?:{alt}))*$")).expect("valid regex")
}

static ONLY_SMALL_TALK: LazyLock<Regex> = LazyLock::new(|| {
    let all: Vec<&str> = THANKS
        .iter()
        .chain(ABOUT.iter())
        .chain(GREETINGS.iter())
        .copied()
        .collect();
    repeated(&all)
});
static THANKS_ONLY: LazyLock<Regex> = LazyLock::new(|| repeated(&THANKS));
static NOT_WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z' ]+").expect("valid regex"));
static SPACES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s+").expect("valid regex"));

pub static INTRO: LazyLock<String> = LazyLock::new(|| {
    let tries: Vec<String> = SKILL_EXAMPLES.iter().map(|e| format!("- {e}")).collect();
    format!(
        "I format structured output as Markdown, Slack blocks or email HTML. Paste the JSON (an envelope \
from another agent, or any JSON) and say which target; I lay it out in code and never change, add \
or summarise content. Then say \"as slack\", \"as email\", \"as markdown\", \"compact\", \"no footer\" \
or \"as json\" to re-render it. I can also convert pasted Markdown to Slack or email.\n\nTry:\n{}",
        tries.join("\n")
    )
});
pub static WELCOME: LazyLock<String> = LazyLock::new(|| format!("You're welcome. {}", *INTRO));

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn greetings_and_about_questions_get_the_intro() {
        for text in [
            "hi",
            "Hello there!",
            "what can you do?",
            "",
            "hey, who are you",
        ] {
            assert_eq!(reply_to(text).as_deref(), Some(INTRO.as_str()), "{text}");
        }
        assert!(
            INTRO.contains("I format structured output as Markdown, Slack blocks or email HTML")
        );
    }

    #[test]
    fn thanks_gets_the_welcome() {
        assert_eq!(reply_to("Thanks!").as_deref(), Some(WELCOME.as_str()));
    }

    #[test]
    fn real_requests_are_not_small_talk() {
        for text in [
            "hi, format this as slack",
            "what's 2+2?",
            "help me write a poem",
        ] {
            assert!(reply_to(text).is_none(), "{text}");
        }
    }
}
