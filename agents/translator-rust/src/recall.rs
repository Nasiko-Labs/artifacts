//! Pure: what earlier turns of this conversation still give a follow-up, read from the platform's history
//! block (this agent stores nothing).
//!
//! - `replay` rebuilds the last job (source text, target, register, glossary) by reading the user turns
//!   oldest to newest with the same rules a live turn uses, without any LLM call. A user turn the agent
//!   declined (the next assistant turn is the off-topic reply) is skipped.
//! - `last_translation` finds the most recent plain-text translation reply and its target language (from
//!   the reply's own footer), for when the original text has been trimmed out of the history.
//!
//! The history is a budgeted slice, so either may be missing; callers then say so and ask for a re-paste.

use std::sync::LazyLock;

use regex::Regex;

use crate::followup::{self, FollowUp};
use crate::history::{ASSISTANT, Turn, USER};
use crate::languages;
use crate::prompts;
use crate::request::{self, Job};
use crate::smalltalk;
use crate::util::{group, re, take_chars};

const DECLINED_PREFIX_CHARS: usize = 25;
static FOOTER: LazyLock<Regex> =
    LazyLock::new(|| re(r"^_(?P<name>.+?) \((?:formal|neutral|casual)\) from [^.]*\."));

/// A new job keeps the conversation's target, register and glossary, never its text.
pub fn carry(stored: Option<&Job>, as_json: bool) -> Job {
    match stored {
        None => Job {
            as_json,
            ..Job::default()
        },
        Some(s) => Job {
            target: s.target.clone(),
            register: s.register,
            keep: s.keep.clone(),
            glossary: s.glossary.clone(),
            as_json,
            ..Job::default()
        },
    }
}

pub fn replay(turns: &[Turn]) -> Option<Job> {
    let mut job: Option<Job> = None;
    for (n, turn) in turns.iter().enumerate() {
        if turn.role != USER || declined(turns, n) {
            continue;
        }
        job = step(job, &turn.text, was_text(turns, n));
    }
    job.map(|j| Job {
        as_json: false,
        ..j
    })
}

/// The job after one earlier user message, as a live turn would have left it. A bare message (no cue)
/// counts as text to translate only when `was_text`: the agent's reply to it shows it was taken as text.
pub fn step(stored: Option<Job>, raw: &str, was_text: bool) -> Option<Job> {
    if smalltalk::reply_to(raw).is_some() {
        return stored;
    }
    let text = prompts::strip_markers(raw);
    let (instruction, body) = request::split(&text);
    let envelope = request::envelope_from(&body);
    if envelope.is_some() || (!instruction.is_empty() && !body.is_empty()) {
        let base = Job {
            source: if envelope.is_some() {
                String::new()
            } else {
                body.clone()
            },
            envelope,
            ..carry(stored.as_ref(), false)
        };
        return Some(request::apply_instruction(base, &instruction));
    }
    if let Some(follow) = followup::parse(&text, stored.as_ref()) {
        return match follow {
            FollowUp::Retranslate(job) => Some(*job),
            FollowUp::Original | FollowUp::Json => stored,
        };
    }
    if !instruction.is_empty() {
        return Some(request::apply_instruction(
            carry(stored.as_ref(), false),
            &instruction,
        ));
    }
    if let (Some(found), Some(job)) = (languages::from_answer(&body), stored.as_ref()) {
        // an answer to "which language?"
        return Some(Job {
            target: found.language.code.to_string(),
            target_note: found.note.to_string(),
            unknown: String::new(),
            ..job.clone()
        });
    }
    if !body.trim().is_empty() && was_text {
        return Some(Job {
            source: body,
            ..carry(stored.as_ref(), false)
        });
    }
    stored
}

/// (translated text, its target language code) of the most recent plain translation reply, or None. The
/// translation is everything before the reply's footer line ("_Spanish (formal) from English. ..._").
pub fn last_translation(turns: &[Turn]) -> Option<(String, String)> {
    turns
        .iter()
        .rev()
        .filter(|t| t.role == ASSISTANT)
        .find_map(|t| split_reply(&t.text))
}

fn split_reply(reply: &str) -> Option<(String, String)> {
    let paragraphs: Vec<&str> = reply.split("\n\n").collect();
    for n in (1..paragraphs.len()).rev() {
        let found = FOOTER
            .captures(paragraphs[n].trim())
            .and_then(|c| languages::from_answer(group(&c, "name")));
        let body = paragraphs[..n].join("\n\n").trim().to_string();
        if let Some(found) = found.filter(|_| !body.is_empty() && !body.starts_with("```")) {
            return Some((body, found.language.code.to_string()));
        }
    }
    None
}

fn next_assistant(turns: &[Turn], n: usize) -> Option<&Turn> {
    turns.get(n + 1).filter(|t| t.role == ASSISTANT)
}

fn was_text(turns: &[Turn], n: usize) -> bool {
    next_assistant(turns, n).is_some_and(|after| {
        let reply = after.text.as_str();
        split_reply(reply).is_some()
            || reply.starts_with("```")
            || reply.starts_with(prompts::ALREADY_PREFIX)
            || reply.contains(prompts::LANGUAGE_QUESTION)
    })
}

fn declined(turns: &[Turn], n: usize) -> bool {
    let prefix = take_chars(&smalltalk::OFF_TOPIC_REPLY, DECLINED_PREFIX_CHARS).to_string();
    next_assistant(turns, n).is_some_and(|after| after.text.starts_with(&prefix))
}
