//! Pure: deciding what one turn does, from the state rebuilt out of the history block (`recall`) and this
//! turn's request. A fixed reply (tone saved, envelope re-rendered, off-topic question, "what should it
//! say?", "paste it again") or a compose job (a new reply, a revision, or a request with nothing to reply
//! to that the model classifies). Nothing ever pauses: a missing purpose is asked for in a plain reply, a
//! missing channel defaults to chat (email when the message has email headers) and the reply says so.
//!
//! Text pasted in this message always wins over the history: an incoming message here starts a new reply.
//! A general-knowledge question is declined in code whatever came before, so an off-topic question asked
//! after a reply is never taken as a revision of it.
//!
//! The user's own words ground facts and links; the incoming message grounds facts only; the examples ground
//! nothing (they are measured in code and never shown to the model).

use std::sync::LazyLock;

use crate::drafting::Job;
use crate::envelope::fenced;
use crate::incoming::Incoming;
use crate::inputs::{self, Parsed, Request};
use crate::recall::State;
use crate::register::detect_channel;
use crate::render;
use crate::smalltalk::OFF_TOPIC_REPLY;
use crate::style::{Metrics, measure};
use crate::util::{Regex, find, is_match, re, split_count};

pub const REDO_INSTRUCTION: &str =
    "Rewrite the current reply in my measured style below. Keep the same meaning and facts.";
pub const MAX_WORDS_KEPT: usize = 20;
pub const DEFAULT_CHANNEL: &str = "chat";
pub const ASK_PURPOSE: &str = concat!(
    "What should the reply say? For example \"say yes\", \"decline politely\", \"ask for more time\", ",
    "\"ask a question\" or \"say thanks\" (or better, in your own words: \"say yes but not before ",
    "Thursday\"). Tell me in your next message and I'll draft it."
);
pub const CHANNEL_NOTE: &str = concat!(
    "I wrote it as a chat message (Slack or Teams). Say \"as an email\" or \"as a text\" to ",
    "change it."
);
pub const REPASTE: &str = concat!(
    "The reply and the message it answers are no longer in the part of our conversation I can see (long ",
    "earlier inputs drop out), so paste the message again and tell me what to say back."
);
pub const TRIMMED_NOTE: &str = concat!(
    "The message you're replying to is no longer in the part of our conversation I can see, so I ",
    "revised from the current reply alone."
);
/// The longest message read as a general-knowledge question.
const QUESTION_MAX_WORDS: usize = 25;

static REVISION_CUE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)^\s*(?:make it|say (?:no|yes) instead|more|less|again|redo|shorter|longer|warmer)\b")
});
static RELAXED: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)\b(?:more|less) (?:formal|casual|friendly|direct|polite)\b|\bwarmer\b|\bfriendlier\b",
        r"|\bcooler\b|\bfirmer\b|\bsofter\b|\blonger\b|\bmore detail"
    ))
});
static SHORT: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\b(?:short(?:er)?|brief(?:er)?|concise|tighter|trim it)\b"));
static REDO: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)^\s*(?:redo|re-?do it|redo (?:it|the reply|this)|apply it)\s*[.!]?\s*$")
});
static JSON_ONLY: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)^\s*(?:(?:show|give|send)(?: me)?(?: it| that| the reply)?\s+)?(?:as json|json only|in json)",
        r"\s*[.!]?\s*$"
    ))
});
static SUM: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)^\s*(?:what(?:'s| is)\s+|calculate\s+|compute\s+)?[\d\s.]+(?:[-+*/x×÷^][\d\s.()]+)+\s*\??\s*$",
    )
});
/// A question about the world ("what's the capital of France?"), as opposed to one about the reply.
static GENERAL_QUESTION: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)^\s*(?:what(?:'s| is| are| was| were| does| do)?|who(?:'s| is| was)?|when(?:'s| is| was| did)?",
        r"|where(?:'s| is| are)?|why(?:'s| is| are| do| does)?|how (?:many|much|far|old|long|tall|big|do|does|is|are)",
        r"|which)\b"
    ))
});
/// Words that tie a message to the reply, the incoming message or the user: such a message is never
/// declined in code (the model decides).
static ABOUT_THE_REPLY: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)\b(?:it|its|this|that|these|those|reply|replies|draft|message|email|mail|text|chat|slack|teams|",
        r"sms|dm|subject|greeting|sign|sign-off|tone|style|version|shorter|longer|formal|casual|warmer|",
        r"friendlier|polite|politely|instead|again|say|tell|ask|offer|decline|accept|agree|propose|suggest|",
        r"refuse|reject|apologi[sz]e|thank|mention|add|remove|drop|include|about|if|not|i|i'm|i'd|i'll|me|my|",
        r"we|we're|our|us|you|your|they|them|their|he|him|his|she|her)\b"
    ))
});

#[derive(Debug, Clone)]
pub enum Plan {
    /// A reply decided in code, with no model call.
    Fixed(String),
    Compose {
        job: Box<Job>,
        notes: Vec<String>,
    },
}

/// A general-knowledge question or a bare sum: out of scope whatever the history holds.
pub fn off_topic_question(text: &str) -> bool {
    if is_match(&SUM, text) {
        return true;
    }
    split_count(text) <= QUESTION_MAX_WORDS
        && is_match(&GENERAL_QUESTION, text)
        && find(&ABOUT_THE_REPLY, text).is_none()
}

pub fn plan_turn(state: &State, request: &Request) -> Plan {
    let text = request.text.as_str();
    let parsed = inputs::parse(text);
    let metrics = if parsed.examples.is_empty() {
        state.metrics.clone()
    } else {
        measure(&parsed.examples)
    };
    let sign_off_as = if parsed.sign_off_as.is_empty() {
        state.sign_off_as.clone()
    } else {
        parsed.sign_off_as.clone()
    };
    if parsed.incoming.is_none() && parsed.examples.is_empty() {
        if off_topic_question(text) {
            return Plan::Fixed(OFF_TOPIC_REPLY.clone());
        }
        if is_match(&JSON_ONLY, text)
            && let Some(envelope) = &state.envelope
        {
            return Plan::Fixed(fenced(envelope));
        }
    }
    if !parsed.examples.is_empty()
        && parsed.incoming.is_none()
        && !parsed.has_purpose
        && !is_match(&REDO, text)
    {
        return Plan::Fixed(render::tone_saved(&metrics, state.reply.is_some()));
    }
    let styled = metrics.with_sign_off(&sign_off_as);
    if let Some(incoming) = parsed.incoming.clone() {
        return new_reply(&parsed, incoming, "", request, styled, "");
    }
    match (&state.reply, &state.incoming) {
        (None, Some(earlier)) => {
            // A message pasted earlier: this turn gives its purpose, or (the draft was trimmed from the
            // history) changes it, so the reply is drafted again from the purpose so far plus this turn.
            let mut answer: Vec<&str> = state.words.iter().map(String::as_str).collect();
            answer.push(text);
            new_reply(
                &parsed,
                earlier.clone(),
                &answer.join("\n"),
                request,
                styled,
                &state.channel,
            )
        }
        (None, None) => {
            if is_match(&REVISION_CUE, text) || (is_match(&SHORT, text) && split_count(text) <= 4) {
                return Plan::Fixed(REPASTE.into());
            }
            let instruction = if parsed.instruction.is_empty() {
                text.to_string()
            } else {
                parsed.instruction.clone()
            };
            let job = Job {
                incoming: None,
                instruction,
                words: vec![text.to_string()],
                org: request.org.clone(),
                channel: "email".into(),
                metrics: styled,
                ..Job::default()
            };
            Plan::Compose {
                job: Box::new(job),
                notes: parsed.notes,
            }
        }
        (Some(_), _) => revision(state, text, &parsed, request, styled),
    }
}

/// A new reply. `answer` is this turn's text when it gives the purpose for a message pasted earlier.
fn new_reply(
    parsed: &Parsed,
    incoming: Incoming,
    answer: &str,
    request: &Request,
    metrics: Metrics,
    earlier_channel: &str,
) -> Plan {
    let own = if parsed.has_purpose && answer.is_empty() {
        parsed.instruction.as_str()
    } else {
        ""
    };
    let purpose = [own, answer]
        .iter()
        .filter(|p| !p.is_empty())
        .copied()
        .collect::<Vec<_>>()
        .join("\n");
    if purpose.is_empty() {
        return Plan::Fixed(ASK_PURPOSE.into());
    }
    let mut notes = parsed.notes.clone();
    let channel = parsed
        .channel
        .or_else(|| detect_channel(&purpose))
        .map(str::to_string)
        .unwrap_or_else(|| earlier_channel.to_string());
    let channel = if channel.is_empty() {
        notes.push(CHANNEL_NOTE.into());
        DEFAULT_CHANNEL.to_string()
    } else {
        channel
    };
    let words: Vec<String> = [parsed.instruction.as_str(), answer]
        .iter()
        .filter(|w| !w.is_empty())
        .map(|w| w.to_string())
        .collect();
    let job = Job {
        incoming: Some(incoming),
        short: is_match(&SHORT, &purpose),
        instruction: purpose,
        words,
        org: request.org.clone(),
        channel,
        metrics,
        want_subject: parsed.want_subject,
        ..Job::default()
    };
    Plan::Compose {
        job: Box::new(job),
        notes,
    }
}

fn revision(
    state: &State,
    text: &str,
    parsed: &Parsed,
    request: &Request,
    metrics: Metrics,
) -> Plan {
    let Some(current) = state.reply.clone() else {
        return Plan::Fixed(REPASTE.into());
    };
    let incoming = state.incoming.clone().unwrap_or_default();
    let redo = is_match(&REDO, text);
    let instruction = if redo { REDO_INSTRUCTION } else { text };
    let named = if redo { None } else { detect_channel(text) };
    let channel = match named {
        Some(channel) => channel.to_string(),
        None if !state.channel.is_empty() => state.channel.clone(),
        None => "email".to_string(),
    };
    let mut words = state.words.clone();
    if !redo {
        words.push(text.to_string());
    }
    let skip = words.len().saturating_sub(MAX_WORDS_KEPT);
    words.drain(..skip);
    let mut notes = parsed.notes.clone();
    if incoming.text.is_empty() {
        // Trimmed out of the history: the current reply (already checked) grounds its own facts.
        words.push(current.draft.clone());
        notes.push(TRIMMED_NOTE.into());
    }
    let job = Job {
        incoming: Some(incoming),
        instruction: instruction.to_string(),
        words,
        org: request.org.clone(),
        channel,
        metrics,
        current: Some(current),
        relaxed: is_match(&RELAXED, text),
        short: is_match(&SHORT, text),
        want_subject: false,
    };
    Plan::Compose {
        job: Box::new(job),
        notes,
    }
}
