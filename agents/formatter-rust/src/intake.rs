//! Pure: what one message asks for, decided in code (there is no model).
//!
//! Order: greeting -> size -> JSON (render) -> broken JSON (reply: fix and paste again) -> follow-up words on
//! the input recalled from the history -> a Markdown conversion request -> anything else is outside scope
//! (fixed decline + intro).
//!
//! Stateless: nothing is stored. A follow-up ("as slack", "compact") re-renders the newest JSON or Markdown the
//! user pasted that is still in the platform's history slice, with the options of that paste and of every
//! follow-up after it replayed in order. Nothing ever pauses.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use crate::followup::{self, Ask};
use crate::history::{Turn, USER};
use crate::inputs::{Incoming, MAX_CHARS};
use crate::model::Options;
use crate::parse::{self, Extracted};
use crate::replies;
use crate::smalltalk;

static FORMAT_INTENT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(?:format|convert|render|turn|transform)\b|\b(?:as|to|into|for|in)\s+(?:a\s+)?(?:slack|e-?mail|html|markdown|md|mrkdwn|block ?kit|blocks)\b",
    )
    .expect("valid regex")
});
const HEAD_MAX_CHARS: usize = 200;

#[derive(Debug, Clone, PartialEq)]
pub enum Route {
    /// A fixed reply, no rendering.
    Reply(String),
    /// Render these JSON values.
    Values(Vec<Value>),
    /// Convert this pasted Markdown.
    Markdown(String),
}

#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    pub route: Route,
    pub opts: Options,
    pub as_json: bool,
    pub plain_version: bool,
}

impl Plan {
    fn reply(text: impl Into<String>) -> Self {
        Self {
            route: Route::Reply(text.into()),
            opts: Options::default(),
            as_json: false,
            plain_version: false,
        }
    }
}

pub fn plan_turn(incoming: &Incoming) -> Plan {
    let text = incoming.text.trim();
    if let Some(canned) = smalltalk::reply_to(text) {
        return Plan::reply(canned);
    }
    let chars = text.chars().count();
    if chars > MAX_CHARS {
        return Plan::reply(replies::too_big(chars, MAX_CHARS));
    }
    let extracted = parse::extract(text);
    if !extracted.values.is_empty() {
        return new_input(&extracted, incoming.wants_json);
    }
    if let Some(error) = &extracted.error {
        return Plan::reply(replies::json_error(&error.describe()));
    }
    if followup::is_followup_only(text) {
        return from_history(text, &incoming.history, incoming.wants_json);
    }
    markdown(text, incoming.wants_json)
        .unwrap_or_else(|| Plan::reply(replies::OFF_TOPIC_REPLY.clone()))
}

fn markdown(text: &str, wants_json: bool) -> Option<Plan> {
    let (head, body) = markdown_request(text)?;
    let ask = followup::read(head);
    Some(Plan {
        route: Route::Markdown(body),
        opts: followup::apply(&ask, &Options::default()),
        as_json: ask.as_json || wants_json,
        plain_version: false,
    })
}

/// The newest user paste still in the history, rendered with its own options and those of the follow-ups
/// after it. None when there is none, or when the newest one is unusable (compressed, or JSON that no
/// longer parses because it was cut): an older paste is never used in its place.
pub fn recall(turns: &[Turn]) -> Option<Plan> {
    let users: Vec<&Turn> = turns.iter().filter(|t| t.role == USER).collect();
    for index in (0..users.len()).rev() {
        let text = users[index].text.trim();
        if followup::is_followup_only(text) || smalltalk::reply_to(text).is_some() {
            continue;
        }
        if users[index].compressed() {
            return None;
        }
        let extracted = parse::extract(text);
        if extracted.error.is_some() {
            return None;
        }
        let found = if extracted.values.is_empty() {
            markdown(text, false)
        } else {
            Some(new_input(&extracted, false))
        };
        let Some(found) = found else { continue };
        let opts = users[index + 1..]
            .iter()
            .filter(|t| followup::is_followup_only(t.text.trim()))
            .map(|t| followup::read(&t.text))
            .fold(found.opts.clone(), |opts, ask| followup::apply(&ask, &opts));
        return Some(Plan { opts, ..found });
    }
    None
}

fn new_input(extracted: &Extracted, wants_json: bool) -> Plan {
    let mut ask: Ask = followup::read(&extracted.instruction);
    if ask.target.is_none() && !extracted.target.is_empty() {
        ask.target = followup::target_of(&extracted.target);
    }
    Plan {
        route: Route::Values(extracted.values.clone()),
        opts: followup::apply(&ask, &Options::default()),
        as_json: ask.as_json || wants_json,
        plain_version: ask.plain_version,
    }
}

fn from_history(text: &str, turns: &[Turn], wants_json: bool) -> Plan {
    let Some(found) = recall(turns) else {
        return if turns.is_empty() {
            Plan::reply(replies::NO_JSON.clone())
        } else {
            Plan::reply(replies::NOT_IN_HISTORY)
        };
    };
    let ask = followup::read(text);
    Plan {
        opts: followup::apply(&ask, &found.opts),
        as_json: ask.as_json || wants_json,
        plain_version: ask.plain_version,
        ..found
    }
}

/// (instruction, markdown body) when the message asks to format pasted Markdown, else None.
/// The instruction is the first line up to a colon ("format as slack: **bold** ...") or the whole
/// first line when the body follows on the next lines.
pub fn markdown_request(text: &str) -> Option<(&str, String)> {
    let (first, rest) = text.split_once('\n').unwrap_or((text, ""));
    if let Some((head, after)) = first.split_once(':')
        && head.chars().count() <= HEAD_MAX_CHARS
        && FORMAT_INTENT.is_match(head)
    {
        let body = format!("{after}\n{rest}").trim().to_owned();
        return (!body.is_empty()).then_some((head, body));
    }
    if first.chars().count() <= HEAD_MAX_CHARS
        && FORMAT_INTENT.is_match(first)
        && !rest.trim().is_empty()
    {
        return Some((first, rest.trim().to_owned()));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::history;
    use crate::model::Target;
    use serde_json::json;

    #[test]
    fn markdown_request_split() {
        assert_eq!(
            markdown_request("format as slack: **bold** text"),
            Some(("format as slack", "**bold** text".into()))
        );
        assert_eq!(
            markdown_request("Convert to email\n# Title\n- a"),
            Some(("Convert to email", "# Title\n- a".into()))
        );
        assert_eq!(markdown_request("write a poem about **cats**"), None);
        assert_eq!(markdown_request("format as slack:"), None);
    }

    #[test]
    fn size_limit_reply() {
        let text = format!("{{\"a\": \"{}\"}}", "x".repeat(100_001));
        let plan = plan_turn(&Incoming {
            text,
            ..Incoming::default()
        });
        assert!(matches!(&plan.route, Route::Reply(r) if r.contains("100,000")));
    }

    #[test]
    fn recall_skips_follow_ups_and_greetings_and_replays_options_in_order() {
        let turns = history::parse(
            "user: hi\nassistant: hello\nuser: as email: {\"a\": 1}\nassistant: x\nuser: compact\nassistant: y\nuser: no footer\nassistant: z",
        );
        let found = recall(&turns).expect("a paste");
        assert_eq!(found.route, Route::Values(vec![json!({"a": 1})]));
        assert_eq!(found.opts.target, Target::Email);
        assert!(found.opts.compact && !found.opts.footer);
    }

    #[test]
    fn recall_refuses_a_compressed_or_cut_paste_and_never_falls_back_to_an_older_one() {
        let old = "user: as slack: {\"a\": 1}\nassistant: ok\n";
        assert_eq!(
            recall(&history::parse(&format!(
                "{old}user: {{\"items\": [1, 2, \"[⋯ 40 of 42 items elided ⋯]\"]}}"
            ))),
            None
        );
        assert_eq!(
            recall(&history::parse(&format!(
                "{old}user: {{\"items\": [1, 2, 3"
            ))),
            None
        );
        assert_eq!(recall(&[]), None);
    }
}
