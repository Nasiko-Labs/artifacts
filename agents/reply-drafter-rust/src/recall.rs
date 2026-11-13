//! Pure: the drafting state rebuilt from the platform's history block (this agent stores nothing).
//!
//! The earlier turns are replayed in order:
//!
//! - a user turn with past replies sets the measured style (the examples are measured, never kept);
//! - a user turn with an incoming message starts a new reply (incoming, channel, the user's words);
//! - any other user turn after that adds its words (they ground facts) and may switch the channel;
//! - an assistant turn rendered by `render::reply_text` (or a fenced JSON envelope) is the current reply.
//!
//! The history is a budgeted slice, so any of these may be missing (trimmed or compressed): the caller then
//! works with what is left, says so, or asks for a re-paste. Nothing is invented: the current reply is the
//! earlier reply's own text, cut out by its markers.

use std::sync::LazyLock;

use serde_json::Value;

use crate::draft::Reply;
use crate::envelope as env;
use crate::history::{Turn, USER};
use crate::incoming::Incoming;
use crate::inputs;
use crate::register::{CHANNELS, detect_channel, register_for};
use crate::smalltalk;
use crate::style::{Metrics, measure};
use crate::tone_check::Target;
use crate::util::{Regex, captures, group, lines, nth, re};

pub const MAX_WORDS_KEPT: usize = 20;
static HEADER: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\*\*Reply to (?P<who>.+?) \((?P<channel>[a-z]+)\)\*\*"));
const SUBJECT: &str = "**Subject:** ";
const SHORTER: &str = "**Shorter:** ";
const TONE: &str = "**Tone notes:**";
static FENCED_JSON: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?s)\A\s*```(?:json)?\s*(\{.*\})\s*```\s*\z"));

/// What the earlier turns left behind, as far as the history still shows it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct State {
    pub metrics: Metrics,
    pub sign_off_as: String,
    pub incoming: Option<Incoming>,
    pub channel: String,
    pub words: Vec<String>,
    pub reply: Option<Reply>,
    /// The current reply's envelope, rebuilt in code (for a later "as json").
    pub envelope: Option<Value>,
}

pub fn state_from(turns: &[Turn]) -> State {
    let mut state = State::default();
    for turn in turns {
        state = if turn.role == USER {
            user(state, &turn.text)
        } else {
            assistant(state, &turn.text)
        };
    }
    let skip = state.words.len().saturating_sub(MAX_WORDS_KEPT);
    state.words.drain(..skip);
    state.envelope = rebuilt_envelope(&state);
    state
}

fn user(state: State, text: &str) -> State {
    if smalltalk::reply_to(text).is_some() {
        return state;
    }
    let parsed = inputs::parse(text);
    let mut next = state;
    if !parsed.examples.is_empty() {
        next.metrics = measure(&parsed.examples);
    }
    if !parsed.sign_off_as.is_empty() {
        next.sign_off_as = parsed.sign_off_as.clone();
    }
    if let Some(incoming) = parsed.incoming {
        let channel = parsed
            .channel
            .or_else(|| detect_channel(&parsed.instruction))
            .unwrap_or("");
        return State {
            incoming: Some(incoming),
            channel: channel.to_string(),
            words: if parsed.has_purpose {
                vec![parsed.instruction]
            } else {
                Vec::new()
            },
            reply: None,
            ..next
        };
    }
    if !parsed.examples.is_empty() || (next.incoming.is_none() && next.reply.is_none()) {
        return next;
    }
    if let Some(channel) = detect_channel(text) {
        next.channel = channel.to_string();
    }
    next.words.push(text.to_string());
    next
}

fn assistant(state: State, text: &str) -> State {
    match rendered_reply(text) {
        Some((reply, channel)) => State {
            reply: Some(reply),
            channel: if channel.is_empty() {
                state.channel.clone()
            } else {
                channel
            },
            ..state
        },
        None => state,
    }
}

/// (the reply, its channel) from an earlier rendered reply or JSON envelope, else None.
pub fn rendered_reply(text: &str) -> Option<(Reply, String)> {
    if let Some(caps) = captures(&FENCED_JSON, text) {
        return from_envelope(nth(&caps, 1));
    }
    let all = lines(text.trim());
    let header = captures(&HEADER, all.first()?)?;
    let mut rest = &all[1..];
    let mut subject = String::new();
    if let Some(line) = rest.first().and_then(|l| l.strip_prefix(SUBJECT)) {
        subject = line.trim().to_string();
        rest = &rest[1..];
    }
    let end = rest
        .iter()
        .position(|l| l.starts_with(SHORTER) || l.starts_with(TONE))
        .unwrap_or(rest.len());
    let draft = rest[..end].join("\n").trim().to_string();
    let shorter = rest[end..]
        .iter()
        .find_map(|l| l.strip_prefix(SHORTER))
        .map_or(String::new(), |s| s.trim().to_string());
    if draft.is_empty() {
        return None;
    }
    let channel = group(&header, "channel");
    let channel = if CHANNELS.contains(&channel) {
        channel
    } else {
        ""
    };
    Some((
        Reply {
            subject,
            draft,
            alt_shorter: (!shorter.is_empty()).then_some(shorter),
            asks: Vec::new(),
        },
        channel.to_string(),
    ))
}

fn from_envelope(blob: &str) -> Option<(Reply, String)> {
    let Ok(Value::Object(data)) = serde_json::from_str::<Value>(blob) else {
        return None;
    };
    let draft = data.get("draft").and_then(Value::as_str)?.trim();
    if draft.is_empty() {
        return None;
    }
    let text = |key: &str| {
        data.get(key)
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    };
    let channel = text("channel");
    let channel = if CHANNELS.contains(&channel.as_str()) {
        channel
    } else {
        String::new()
    };
    let alt = text("alt_shorter");
    Some((
        Reply {
            subject: text("subject"),
            draft: draft.to_string(),
            alt_shorter: (!alt.is_empty()).then_some(alt),
            asks: Vec::new(),
        },
        channel,
    ))
}

/// The envelope of the current reply, computed in code (for a later "as json"), else None.
pub fn rebuilt_envelope(state: &State) -> Option<Value> {
    let reply = state.reply.as_ref()?;
    let metrics = state.metrics.with_sign_off(&state.sign_off_as);
    let channel = if state.channel.is_empty() {
        "email"
    } else {
        state.channel.as_str()
    };
    let sender = state
        .incoming
        .as_ref()
        .map_or(String::new(), |i| i.sender.clone());
    let target = Target {
        metrics: metrics.clone(),
        register: register_for(channel),
        sender: sender.clone(),
        relaxed: false,
        short: false,
    };
    let level = env::confidence(&metrics, reply, &target, false, false);
    let notes = env::tone_notes(&metrics, &target, reply);
    Some(env::build(reply, channel, &sender, &metrics, &notes, level))
}
