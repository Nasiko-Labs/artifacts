//! The formatter turn, in code (zero model calls):
//!
//! ```text
//! intake ─┬─ render (JSON or Markdown -> the target)
//!         └─ fixed reply
//! ```
//!
//! It holds nothing: every call starts from this turn's input alone. "as slack", "as email" or "compact"
//! re-render the input recalled from the platform's history slice (intake::recall), so any replica can
//! answer any message. Nothing pauses.

use serde_json::Value;

use crate::envelope;
use crate::inputs::Incoming;
use crate::intake::{self, Route};
use crate::output;

#[derive(Debug, Clone, PartialEq)]
pub struct TurnResult {
    pub reply: String,
    pub envelope: Option<Value>,
}

pub fn turn(incoming: &Incoming) -> TurnResult {
    let plan = intake::plan_turn(incoming);
    let rendering = match &plan.route {
        Route::Reply(text) => {
            return TurnResult {
                reply: with_notes(text, &incoming.notes),
                envelope: None,
            };
        }
        Route::Values(values) => envelope::render_values(values, &plan.opts),
        Route::Markdown(source) => envelope::render_markdown(source, &plan.opts),
    };
    let result = output::reply(&rendering, plan.as_json, plan.plain_version);
    TurnResult {
        reply: with_notes(&result.text, &incoming.notes),
        envelope: result.envelope,
    }
}

fn with_notes(text: &str, notes: &[String]) -> String {
    std::iter::once(text)
        .chain(notes.iter().map(String::as_str))
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixtures;
    use crate::history::{self, Turn};
    use crate::pyjson;
    use crate::replies;
    use crate::smalltalk::INTRO;

    /// Plays the platform: every message carries the earlier turns as the history slice, built and parsed
    /// exactly as on the wire. The agent itself keeps nothing.
    type Turns = Vec<(String, String)>;

    struct Chat {
        turns: Turns,
        keep: fn(&[(String, String)]) -> Turns,
    }

    impl Chat {
        fn new() -> Self {
            Self {
                turns: Vec::new(),
                keep: |t| t.to_vec(),
            }
        }

        fn say(&mut self, text: &str) -> TurnResult {
            let kept = (self.keep)(&self.turns);
            let block: Vec<String> = kept.iter().map(|(r, t)| format!("{r}: {t}")).collect();
            let raw = if kept.is_empty() {
                text.to_owned()
            } else {
                format!("{}\n\nCurrent message: {text}", block.join("\n"))
            };
            let (block, current) = history::split(&raw);
            let result = turn(&Incoming {
                text: current.trim().to_owned(),
                history: history::parse(block),
                ..Incoming::default()
            });
            self.turns.push(("user".into(), text.into()));
            self.turns.push(("assistant".into(), result.reply.clone()));
            result
        }
    }

    fn once(text: &str) -> TurnResult {
        turn(&Incoming {
            text: text.into(),
            ..Incoming::default()
        })
    }

    fn paste() -> String {
        format!(
            "Format this as Slack:\n```json\n{}\n```",
            pyjson::dumps(&fixtures::thread_untangler())
        )
    }

    fn env(result: &TurnResult, key: &str) -> Value {
        result
            .envelope
            .as_ref()
            .map_or(Value::Null, |e| e[key].clone())
    }

    #[test]
    fn greeting_is_the_intro() {
        for text in ["hi", "Hello there!", "what can you do?", ""] {
            let result = once(text);
            assert_eq!(result.reply, *INTRO);
            assert!(result.envelope.is_none());
        }
    }

    #[test]
    fn anything_else_is_declined_with_the_intro() {
        for text in [
            "What's 2+2?",
            "write a poem",
            "What's the weather?",
            "summarize this thread",
        ] {
            let result = once(text);
            assert_eq!(result.reply, *replies::OFF_TOPIC_REPLY);
            assert!(!result.reply.contains('4'));
        }
    }

    #[test]
    fn a_paste_renders_and_follow_ups_reuse_the_input_from_history() {
        let mut chat = Chat::new();
        let first = chat.say(&paste());
        assert_eq!(env(&first, "target"), "slack");
        assert!(
            first.reply.starts_with("```json\n{")
                && first.reply.contains("Rendered as Slack blocks")
        );
        let email = chat.say("as email");
        assert!(env(&email, "target") == "email" && email.reply.contains("```html"));
        assert!(
            chat.say("show the plain-text version")
                .reply
                .starts_with("Plain-text version:")
        );
        let compact = chat.say("as markdown, compact");
        assert!(env(&compact, "target") == "markdown" && !compact.reply.contains("| ---"));
        assert!(
            compact
                .reply
                .contains("1. **Task:** Update the pricing page"),
            "{}",
            compact.reply
        );
        let no_footer = chat.say("no footer");
        assert!(
            !env(&no_footer, "formatted")
                .as_str()
                .unwrap_or_default()
                .contains("Confidence:")
        );
        assert!(
            !no_footer.reply.contains("| ---"),
            "compact replayed from the earlier follow-up"
        );
        let as_json = chat.say("as json");
        let inner = as_json
            .reply
            .strip_prefix("```json\n")
            .and_then(|r| r.strip_suffix("\n```"))
            .unwrap_or_default();
        let shown: Value = serde_json::from_str(inner).expect("fenced envelope");
        assert_eq!(Some(&shown), as_json.envelope.as_ref());
        let keys: Vec<&str> = shown
            .as_object()
            .map(|m| m.keys().map(String::as_str).collect())
            .unwrap_or_default();
        assert_eq!(
            keys,
            [
                "summary",
                "items",
                "formatted",
                "target",
                "plain_text",
                "notes",
                "confidence",
                "needs_human"
            ]
        );
    }

    #[test]
    fn follow_up_without_any_history_asks_for_json() {
        assert_eq!(once("as slack").reply, *replies::NO_JSON);
    }

    #[test]
    fn generic_json_and_needs_human_copied() {
        let generic = once("{\"a\":1}");
        assert!(env(&generic, "confidence") == "medium" && generic.reply.contains("**A:** 1"));
        assert_eq!(
            env(&once(&pyjson::dumps(&fixtures::promises())), "needs_human"),
            true
        );
    }

    #[test]
    fn metadata_output_json_gives_the_envelope() {
        let result = turn(&Incoming {
            text: pyjson::dumps(&fixtures::ranked()),
            wants_json: true,
            ..Incoming::default()
        });
        let inner = result
            .reply
            .strip_prefix("```json\n")
            .and_then(|r| r.strip_suffix("\n```"))
            .unwrap_or_default();
        assert_eq!(
            serde_json::from_str::<Value>(inner)
                .ok()
                .map(|v| v["target"].clone()),
            Some(Value::from("markdown"))
        );
    }

    #[test]
    fn markdown_input_converted_and_followup_on_it() {
        let mut chat = Chat::new();
        let result = chat.say("format as slack: **bold** and [docs](https://example.com)");
        assert!(
            env(&result, "target") == "slack"
                && env(&result, "formatted")
                    .as_str()
                    .unwrap_or_default()
                    .contains("*bold*")
        );
        let again = chat.say("as email");
        assert!(
            env(&again, "formatted")
                .as_str()
                .unwrap_or_default()
                .contains("<strong>bold</strong>")
        );
    }

    #[test]
    fn broken_json_is_a_plain_reply_never_a_pause() {
        let broken = once("as slack: {\"summary\": \"s\", \"items\": [1, 2,]}");
        assert!(
            broken
                .reply
                .starts_with("That JSON didn't parse (JSON error near character")
                && broken.envelope.is_none()
        );
        let mut chat = Chat::new();
        chat.say("as slack: {\"summary\": \"s\", \"items\": [1, 2,]}");
        assert_eq!(
            env(
                &chat.say("as slack: {\"summary\": \"s\", \"items\": [1, 2]}"),
                "target"
            ),
            "slack"
        );
    }

    #[test]
    fn follow_ups_re_render_the_paste_from_history() {
        let mut chat = Chat::new();
        chat.say(&paste());
        let compact = chat.say("as markdown, compact");
        assert!(
            compact
                .reply
                .contains("1. **Task:** Update the pricing page")
        );
        let still = chat.say("no footer");
        assert!(env(&still, "target") == "markdown" && !still.reply.contains("| ---"));
        assert!(
            !env(&still, "formatted")
                .as_str()
                .unwrap_or_default()
                .contains("Confidence:")
        );
    }

    #[test]
    fn follow_up_with_the_paste_trimmed_from_history_asks_to_paste_again() {
        let mut chat = Chat {
            turns: Vec::new(),
            keep: |t| t[t.len().saturating_sub(2)..].to_vec(),
        };
        chat.say(&paste());
        assert_eq!(env(&chat.say("as email"), "target"), "email");
        assert_eq!(chat.say("as markdown").reply, replies::NOT_IN_HISTORY);
        let mut squeezed = Chat {
            turns: Vec::new(),
            keep: |t| {
                t.iter()
                    .map(|(r, x)| (r.clone(), crate::limits::take_chars(x, 40).to_owned()))
                    .collect()
            },
        };
        squeezed.say(&paste());
        assert_eq!(squeezed.say("as email").reply, replies::NOT_IN_HISTORY);
    }

    #[test]
    fn history_turns_without_a_marker_are_ignored() {
        let incoming = Incoming {
            text: "as slack".into(),
            history: vec![Turn::new("assistant", "x")],
            ..Incoming::default()
        };
        assert_eq!(turn(&incoming).reply, replies::NOT_IN_HISTORY);
    }

    #[test]
    fn file_notes_are_appended() {
        let incoming = Incoming {
            text: "hi".into(),
            notes: vec!["I can't read a.png (image/png); paste its JSON instead.".into()],
            ..Incoming::default()
        };
        assert!(turn(&incoming).reply.ends_with("paste its JSON instead."));
    }
}
