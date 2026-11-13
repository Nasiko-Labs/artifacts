//! Follow-ups: re-render commands parsed in code (zero LLM calls), or one quote-checked question about a
//! text still in the conversation history, or an earlier summary reply (one call, plus one repair).
//! Nothing is stored between messages.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use crate::checks;
use crate::grounding as g;
use crate::llm::{self, LlmError, TurnDeps};
use crate::prompts;
use crate::render::View;
use crate::smalltalk;
use crate::util::{collapse_ws, re};

pub const ASK_STATUSES: &[&str] = &["ok", "not_in_input", "off_topic"];
pub const COMMAND_MAX_WORDS: usize = 10;
const ANSWER_MAX_WORDS: usize = 80;
const NO_TEXT_MATERIAL: &str = "(no text has been provided yet)";

static FILLER: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"\b(?:please|pls|can you|could you|would you|show me|give me|just|only|me|now|again|the|",
        r"list|show|what (?:are|were)|with|include|including|and|in|into|it|as|a)\b"
    ))
});
static SOURCES: LazyLock<Regex> =
    LazyLock::new(|| re(r"\b(?:sources?|quotes?|evidence|citations?|receipts)\b"));
static COMMANDS: LazyLock<Vec<(View, Regex)>> = LazyLock::new(|| {
    vec![
        (
            View::Owners,
            re(r"\bwho (?:owns|is doing|does) what\b|\bwho's doing what\b|\bby owner\b|^owners?$"),
        ),
        (View::Table, re(r"^(?:table|tabular|tabular form)$")),
        (
            View::Actions,
            re(r"^(?:action items?|actions?|todos?|to-?dos|tasks|next steps)(?: only)?$"),
        ),
        (
            View::Decisions,
            re(r"^(?:decisions?|what was decided|decided)(?: only)?$"),
        ),
        (
            View::Tldr,
            re(
                r"^(?:shorter|tl ?;? ?dr|tldr|brief(?:er)?|short(?:er)? version|summary only)(?: only)?$",
            ),
        ),
    ]
});
static SUMMARIZE_WORDS: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"\b(?:summari[sz]e|summary|tl ?;? ?dr|recap|key points|action items|minutes|",
        r"digest|sum up|boil (?:it|this) down)\b"
    ))
});
static QUESTION_START: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|does|did|can|",
        r"could|should|would|will|tell|write|translate|explain|give|calculate|compute|list|",
        r"name|define|describe|compose|draft|generate|create|make)\b"
    ))
});
static WORDS: LazyLock<Regex> = LazyLock::new(|| re(r"[^a-z0-9;' ]+"));

fn plain(text: &str) -> String {
    collapse_ws(&WORDS.replace_all(&text.to_lowercase(), " "))
}

/// (view, with_sources) for a short re-render message, or None. "show sources" alone is the full summary
/// with quotes; "only the action items, with sources" is one section with quotes.
pub fn parse_command(text: &str) -> Option<(View, bool)> {
    let plain = plain(text);
    if plain.is_empty() || plain.split_whitespace().count() > COMMAND_MAX_WORDS {
        return None;
    }
    let sources = SOURCES.is_match(&plain) && !QUESTION_START.is_match(&plain);
    let core = collapse_ws(&SOURCES.replace_all(&FILLER.replace_all(&plain, " "), " "));
    for (view, pattern) in COMMANDS.iter() {
        let target = if *view == View::Owners { &plain } else { &core };
        if pattern.is_match(target) {
            return Some((*view, sources));
        }
    }
    (sources && core.is_empty()).then_some((View::Default, true))
}

pub fn is_summarize_request(text: &str) -> bool {
    SUMMARIZE_WORDS.is_match(&plain(text))
}

pub fn is_question_like(text: &str) -> bool {
    text.trim().ends_with('?') || QUESTION_START.is_match(&plain(text))
}

/// One quote-checked answer from `source`: a text from the history, or an earlier summary reply.
pub async fn ask(deps: &TurnDeps, question: &str, source: &str, context: &[String]) -> String {
    let material = if source.is_empty() {
        NO_TEXT_MATERIAL
    } else {
        source
    };
    let messages = prompts::ask_messages("SOURCE_TEXT", material, question, context);
    let parsed = deps
        .complete_json(
            &messages,
            prompts::ASK_CONTRACT,
            parse_answer,
            llm::MAX_TOKENS,
        )
        .await;
    let parsed = match parsed {
        Ok(parsed) => parsed,
        Err(LlmError::Transport(_)) => return prompts::MODEL_FAILED.to_string(),
        Err(LlmError::BadResponse(_) | LlmError::Contract) => {
            return prompts::MODEL_UNUSABLE.to_string();
        }
        Err(_) => return prompts::OUT_OF_TIME.to_string(),
    };
    if parsed.status == "off_topic" {
        return smalltalk::OFF_TOPIC_REPLY.clone();
    }
    if source.is_empty() {
        return smalltalk::NO_TEXT_YET.clone();
    }
    answer_text(&parsed, source)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Answer {
    pub status: String,
    pub answer: String,
    pub snippet: String,
}

pub fn parse_answer(data: &Value) -> Option<Answer> {
    let status = data.get("status").and_then(Value::as_str)?;
    if !ASK_STATUSES.contains(&status) {
        return None;
    }
    Some(Answer {
        status: status.to_string(),
        answer: checks::value_text(data.get("answer")),
        snippet: checks::value_text(data.get("snippet")),
    })
}

/// `ok` stands only when its quote is in the source and the answer adds no number or date of its own.
pub fn answer_text(parsed: &Answer, source: &str) -> String {
    let norm = g::normalise(source);
    let answer = checks::clean_text(&parsed.answer, source);
    let snippet = collapse_ws(&parsed.snippet);
    let backed = parsed.status == "ok"
        && !answer.is_empty()
        && g::locate(&snippet, &norm).is_some()
        && g::ungrounded_tokens(&answer, &norm).is_empty();
    if !backed {
        return prompts::NOT_IN_INPUT.to_string();
    }
    let quote = checks::clean_text(&checks::cap_snippet(&snippet), &snippet);
    format!(
        "{}\n\n> \"{quote}\"",
        checks::cut_words(&answer, ANSWER_MAX_WORDS)
    )
}
