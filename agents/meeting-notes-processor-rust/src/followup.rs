//! Follow-ups: view commands parsed in code (zero LLM calls), or one quote-checked question about a meeting
//! still in the conversation history (one call, plus one repair). Nothing is stored between messages.
//!
//! A question always goes to the model with the SCOPE rule, even right after a set of notes: an off-topic
//! question ("what's 2+2?") is declined there, never answered from the history in code.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use crate::checks::{self, Index};
use crate::chunking;
use crate::grounding as g;
use crate::items::{Turn, UNKNOWN};
use crate::llm::{self, LlmError, TurnDeps};
use crate::process;
use crate::prompts;
use crate::render::View;
use crate::smalltalk;
use crate::util::{collapse_ws, re};

pub const ASK_STATUSES: &[&str] = &["ok", "not_in_input", "off_topic"];
pub const COMMAND_MAX_WORDS: usize = 10;
pub const ANSWER_MAX_WORDS: usize = 80;
pub const NO_MATERIAL: &str = "(no meeting has been provided yet)";

static FILLER: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"\b(?:please|pls|can you|could you|would you|show me|give me|just|only|me|now|again|the|",
        r"list|show|with|include|including|and|in|it|a|all|full)\b"
    ))
});
static COMMANDS: LazyLock<Vec<(View, Regex)>> = LazyLock::new(|| {
    vec![
        (View::Json, re(r"^(?:as |in )?json(?: only)?$")),
        (
            View::Table,
            re(r"^(?:as |in )?(?:a )?(?:table|tabular(?: form)?)$"),
        ),
        (
            View::Mine,
            re(concat!(
                r"^(?:mine|my (?:action items?|actions?|tasks|items|follow ?ups?)|what do i own|",
                r"what'?s mine|what am i on the hook for)$"
            )),
        ),
        (View::Followups, re(r"^(?:follow ?-? ?ups?)$")),
        (
            View::Actions,
            re(r"^(?:action items?|actions?|todos?|to-?dos|tasks|next steps)$"),
        ),
        (View::Decisions, re(r"^(?:decisions?|what was decided)$")),
        (
            View::Sources,
            re(r"^(?:sources?|quotes?|evidence|citations?|receipts)$"),
        ),
        (View::Notes, re(r"^(?:notes|topics|topic notes)$")),
        (
            View::Default,
            re(r"^(?:everything|again|notes again|whole thing|minutes)$"),
        ),
    ]
});
static PROCESS_WORDS: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"\b(?:process|transcript|meeting notes|minutes|action items|notes from|",
        r"summari[sz]e (?:the|this|my) (?:meeting|call|sync))\b"
    ))
});
static QUESTION_START: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|does|did|can|",
        r"could|should|would|will|tell|write|translate|explain|give|calculate|compute|list|",
        r"name|define|describe|compose|draft|generate|create|make)\b"
    ))
});
static WORDS: LazyLock<Regex> = LazyLock::new(|| re(r"[^a-z0-9' -]+"));

fn plain(text: &str) -> String {
    collapse_ws(&WORDS.replace_all(&text.to_lowercase(), " "))
}

/// The view for a short re-render message ("only mine", "as a table", "show sources"), or None.
pub fn parse_command(text: &str) -> Option<View> {
    let plain = plain(text);
    if plain.is_empty() || plain.split_whitespace().count() > COMMAND_MAX_WORDS {
        return None;
    }
    let stripped = collapse_ws(&FILLER.replace_all(&plain, " "));
    let core = if stripped.is_empty() { plain } else { stripped };
    COMMANDS
        .iter()
        .find(|(_, pattern)| pattern.is_match(&core))
        .map(|(view, _)| *view)
}

pub fn is_process_request(text: &str) -> bool {
    PROCESS_WORDS.is_match(&plain(text))
}

pub fn is_question_like(text: &str) -> bool {
    text.trim().ends_with('?') || QUESTION_START.is_match(&plain(text))
}

/// One quote-checked answer from `turns`: a transcript's turns, or (name NOTES) the lines of an earlier
/// notes reply, whose line ids mean nothing to the user and so are not cited (`cite = false`).
pub async fn ask(
    deps: &TurnDeps,
    question: &str,
    turns: &[Turn],
    name: &str,
    context: &[String],
    cite: bool,
) -> String {
    let material = if turns.is_empty() {
        NO_MATERIAL.to_string()
    } else {
        chunking::as_text(turns)
    };
    let messages = prompts::ask_messages(name, &material, question, context);
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
        Err(err) => return failed_reply(&err),
    };
    if parsed.status == "off_topic" {
        return smalltalk::OFF_TOPIC_REPLY.clone();
    }
    if turns.is_empty() {
        return smalltalk::NO_TEXT_YET.clone();
    }
    answer_text(&parsed, turns, cite)
}

fn failed_reply(err: &LlmError) -> String {
    process::fixed_reply(process::failure(err)).unwrap_or_else(|| prompts::MODEL_FAILED.into())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Answer {
    pub status: String,
    pub answer: String,
    pub turn_id: String,
    pub quote: String,
}

pub fn parse_answer(data: &Value) -> Option<Answer> {
    let status = data.get("status").and_then(Value::as_str)?;
    if !ASK_STATUSES.contains(&status) {
        return None;
    }
    Some(Answer {
        status: status.to_string(),
        answer: checks::value_text(data.get("answer")),
        turn_id: checks::value_text(data.get("turn_id")),
        quote: checks::value_text(data.get("quote")),
    })
}

/// `ok` stands only when its quote is in the cited turn (or exactly one other) and the answer adds no
/// number or date of its own.
pub fn answer_text(parsed: &Answer, turns: &[Turn], cite: bool) -> String {
    let index = Index::new(turns, &[]);
    let answer = checks::clean_text(&parsed.answer, &index.source);
    let backed = parsed.status == "ok"
        && !answer.is_empty()
        && g::ungrounded_tokens(&answer, &index.norm_source).is_empty();
    if !backed {
        return prompts::NOT_IN_INPUT.to_string();
    }
    let Some(found) =
        checks::find_quote(&parsed.quote, std::slice::from_ref(&parsed.turn_id), &index)
    else {
        return prompts::NOT_IN_INPUT.to_string();
    };
    let Some(turn) = index.by_id.get(&found[0]).and_then(|&i| turns.get(i)) else {
        return prompts::NOT_IN_INPUT.to_string();
    };
    let quote = checks::clean_text(&checks::cap_quote(&parsed.quote), &parsed.quote);
    let source = if cite {
        let when = if turn.time.is_empty() {
            String::new()
        } else {
            format!(", {}", turn.time)
        };
        let who = if turn.speaker == UNKNOWN {
            String::new()
        } else {
            format!("{}, ", turn.speaker)
        };
        format!(" ({who}{}{when})", turn.id)
    } else {
        String::new()
    };
    format!(
        "{}\n\n> \"{quote}\"{source}",
        checks::cut_words(&answer, ANSWER_MAX_WORDS)
    )
}
