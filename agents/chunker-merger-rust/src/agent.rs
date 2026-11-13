//! The chunker-merger turn, in code (zero model calls, zero network), stateless:
//!
//! ```text
//! intake (classify; a plain paste after an unfinished request completes it)
//!   -> run (split | a piece | merge | follow-up; every split verified losslessly)
//! ```
//!
//! Nothing is stored: the input is this turn's message only, and follow-ups ("show part 3", "split again
//! with 10,000 chars", "merge with this one") recompute from the input found in the platform's history slice
//! (recall.rs), so any replica can answer any message. Nothing ever pauses: a missing text or result, or a
//! text over the cap, gets a plain reply.

use serde_json::Value;

use crate::actions::{self, Out};
use crate::classify::{Kind, classify};
use crate::envelope::extract_values;
use crate::followup;
use crate::inputs::Incoming;
use crate::recall;
use crate::replies;

#[derive(Debug, Clone, PartialEq)]
pub struct TurnResult {
    pub reply: String,
    pub envelope: Option<Value>,
}

/// One turn. An `Err` is an internal failure (a split that failed its own lossless check twice); the task
/// then ends `failed`.
pub fn turn(incoming: &Incoming) -> Result<TurnResult, String> {
    let previous = recall::previous_request(&incoming.history);
    let request = recall::resolve(
        classify(&incoming.text),
        &incoming.text,
        previous.as_ref().map(|(t, r)| (t, r)),
    );
    let out = match request.kind {
        Kind::Intro => Out::reply(request.reply.clone()),
        kind if kind.is_follow_up() => followup::handle(
            &request,
            &recall::recall(&incoming.history),
            !incoming.history.is_empty(),
        )?,
        Kind::Split => actions::split_request(&request.payload, &request.opts)?,
        Kind::Append | Kind::End => {
            actions::piece(&request.payload, &request.opts, request.kind == Kind::End)?
        }
        Kind::Merge => actions::merge_values(&extract_values(&request.payload), &[]),
        _ => Out::reply(replies::OFF_TOPIC_REPLY.clone()),
    };
    Ok(TurnResult {
        reply: with_notes(&out.reply, &incoming.notes),
        envelope: out.envelope,
    })
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
    use crate::inputs::read_parts;
    use crate::pyjson;
    use crate::smalltalk::INTRO;
    use a2a::Part;

    type Turns = Vec<(String, String)>;

    /// Plays the platform: every message carries the earlier turns as the history slice, built and parsed
    /// exactly as on the wire. The agent itself keeps nothing.
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
            let result = turn(&read_parts(&[Part::text(raw)])).expect("a turn");
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
        .expect("a turn")
    }

    fn fenced_json(reply: &str) -> Value {
        let inner = reply
            .split_once("```json\n")
            .and_then(|(_, r)| r.split_once("\n```"))
            .map(|(j, _)| j)
            .unwrap_or_default();
        serde_json::from_str(inner).expect("fenced JSON")
    }

    fn items(result: &TurnResult) -> Vec<Value> {
        result
            .envelope
            .as_ref()
            .and_then(|e| e["items"].as_array().cloned())
            .unwrap_or_default()
    }

    #[test]
    fn greeting_and_decline() {
        assert_eq!(once("hi").reply, *INTRO);
        for text in ["What's 2+2?", "summarise this"] {
            let reply = once(text).reply;
            assert!(
                reply.starts_with("That's outside what I do") && reply.contains(INTRO.as_str())
            );
        }
    }

    #[test]
    fn demo_split_four_parts_by_turns_with_a_step() {
        let text = fixtures::transcript(70_000, 7);
        let mut chat = Chat::new();
        let result = chat.say(&format!(
            "Split into 4 parts by turns, for each part extract action items:\n{text}"
        ));
        assert!(
            result.reply.starts_with("Split 7") && result.reply.contains("into 4 parts (by turns")
        );
        assert!(
            result
                .reply
                .contains("=== PART 1/4 ===\nextract action items\n")
        );
        assert!(
            result.reply.contains("PART 2/4 · ") && result.reply.contains("(say \"show part 2\")")
        );
        assert!(
            result.reply.contains("Merge instruction:") && result.reply.contains("characters / 4")
        );
        assert_eq!(items(&result).len(), 4);
        assert_eq!(
            result.envelope.as_ref().map(|e| e["confidence"].clone()),
            Some(Value::from("high"))
        );
        let part3 = chat.say("show part 3").reply;
        assert!(
            part3.starts_with("=== PART 3/4 ===") && items(&result)[2]["prompt"] == part3.as_str()
        );
        let listing = chat.say("list the parts").reply;
        assert!(listing.matches("- PART ").count() == 4 && listing.contains("turns 1-"));
        assert_eq!(fenced_json(&chat.say("as json").reply)["items"][0]["n"], 1);
        let again = chat.say("split again with 10,000 chars");
        assert!(
            items(&again).len() >= 7
                && items(&again)[0]["prompt"]
                    .as_str()
                    .is_some_and(|p| p.contains("extract action items"))
        );
        assert!(
            chat.say("show part 99")
                .reply
                .starts_with("There's no part 99")
        );
        assert_eq!(
            items(&chat.say("as json")).len(),
            items(&again).len(),
            "resplit replayed"
        );
    }

    #[test]
    fn short_split_shows_every_part_in_full() {
        let result = once(&format!(
            "split into 2 parts\n{}",
            fixtures::transcript(9_000, 7)
        ));
        assert_eq!(result.reply.matches("<<<TEXT>>>").count(), 2);
    }

    #[test]
    fn split_without_text_is_a_plain_ask_and_the_next_paste_is_split_with_its_options() {
        let mut chat = Chat::new();
        let asked = chat.say("split into 2 parts by turns");
        assert!(asked.reply == replies::NEED_TEXT && asked.envelope.is_none());
        assert!(
            chat.say(&fixtures::transcript(8_000, 7))
                .reply
                .contains("into 2 parts (by turns")
        );
        assert!(
            Chat::new()
                .say("ok")
                .reply
                .starts_with("That's outside what I do")
        );
    }

    #[test]
    fn pieces_over_messages_are_split_as_they_arrive() {
        let text = fixtures::transcript(30_000, 7);
        let (a, b) = text.split_at(15_000);
        let mut chat = Chat::new();
        let first = chat.say(&format!(
            "part 1 of 2, more coming, split into 3 parts\n{a}"
        ));
        assert!(first.reply.contains("into 3 parts") && first.reply.contains(replies::PIECE_NOTE));
        let done = chat.say(&format!("part 2 of 2, split into 3 parts\n{b}"));
        assert!(items(&done).len() == 3 && done.reply.contains(replies::PIECE_NOTE));
        assert_eq!(chat.say("that's all").reply, replies::NOTHING_HELD);
    }

    #[test]
    fn a_text_over_the_cap_is_split_up_to_the_cap_and_says_what_was_left_out() {
        let text = fixtures::transcript(30_000, 7);
        let opts = crate::split::SplitOptions {
            parts: Some(2),
            ..Default::default()
        };
        let done = actions::split_capped(&text, &opts, &[], 20_000).expect("a split");
        let kept: u64 = done
            .envelope
            .as_ref()
            .and_then(|e| e["items"].as_array().cloned())
            .unwrap_or_default()
            .iter()
            .filter_map(|i| i["chars"].as_u64())
            .sum();
        assert!(kept <= 20_000 && done.reply.contains("were left out"));
    }

    #[test]
    fn merge_demo_then_follow_ups() {
        let (a, b) = (
            fixtures::part(1),
            fixtures::summarizer_part(2, "low", false),
        );
        let message = format!(
            "merge these\n```json\n{}\n```\n```json\n{}\n```",
            pyjson::dumps(&a),
            pyjson::dumps(&b)
        );
        let mut chat = Chat::new();
        let result = chat.say(&message);
        let merged = fenced_json(&result.reply);
        assert_eq!(Some(&merged), result.envelope.as_ref());
        assert_eq!(
            (merged["confidence"].clone(), merged["merged_parts"].clone()),
            (Value::from("low"), Value::from(2))
        );
        assert!(result.reply.contains(
            "Merge notes: 1 duplicate decisions removed; 1 duplicate action_items removed"
        ));
        assert!(
            chat.say("show the merge notes")
                .reply
                .starts_with("Merge notes:\n- 1 duplicate decisions removed")
        );
        let more = chat.say(&format!(
            "merge with this one\n{}",
            pyjson::dumps(&fixtures::summarizer_part(3, "high", true))
        ));
        let envelope = more.envelope.clone().unwrap_or_default();
        assert_eq!(
            (
                envelope["merged_parts"].clone(),
                envelope["needs_human"].clone()
            ),
            (Value::from(3), Value::from(true))
        );
        assert_eq!(fenced_json(&chat.say("as json").reply)["merged_parts"], 3);
    }

    #[test]
    fn merge_with_one_result_is_a_plain_ask_and_the_next_paste_completes_it() {
        let mut chat = Chat::new();
        let asked = chat.say(&format!(
            "merge these: {}",
            pyjson::dumps(&fixtures::ranked_1())
        ));
        assert!(asked.reply.contains(replies::NEED_ENVELOPES) && asked.envelope.is_none());
        let done = chat.say(&pyjson::dumps(&fixtures::ranked_2()));
        let ranks: Vec<u64> = done.envelope.unwrap_or_default()["ranked"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|r| r["rank"].as_u64())
            .collect();
        assert_eq!(ranks, [1, 2, 3]);
    }

    #[test]
    fn merge_of_different_agents_is_refused() {
        let message = format!(
            "merge {} {}",
            pyjson::dumps(&fixtures::part(1)),
            pyjson::dumps(&fixtures::ranked_1())
        );
        assert!(
            once(&message)
                .reply
                .starts_with("These look like results from different agents")
        );
    }

    #[test]
    fn follow_ups_without_history() {
        assert!(once("show part 3").reply.starts_with("There's no split"));
        assert!(
            once("show the merge notes")
                .reply
                .starts_with("There's no merge")
        );
    }

    #[test]
    fn a_fresh_agent_shows_the_same_part_from_history() {
        let mut chat = Chat::new();
        let first = chat.say(&format!(
            "split into 3 parts\n{}",
            fixtures::transcript(30_000, 7)
        ));
        assert_eq!(
            Value::from(chat.say("show part 2").reply),
            items(&first)[1]["prompt"]
        );
    }

    #[test]
    fn follow_ups_with_the_source_trimmed_or_compressed_ask_to_paste_again() {
        let text = fixtures::transcript(30_000, 7);
        let mut gone = Chat {
            turns: Vec::new(),
            keep: |t| t[t.len().saturating_sub(2)..].to_vec(),
        };
        gone.say(&format!("split into 3 parts\n{text}"));
        gone.say("list the parts");
        assert_eq!(gone.say("show part 2").reply, replies::SPLIT_NOT_IN_HISTORY);
        let mut squeezed = Chat {
            turns: Vec::new(),
            keep: |t| {
                t.iter()
                    .map(|(r, x)| {
                        (
                            r.clone(),
                            format!(
                                "{} [⋯ 40 lines elided ⋯]",
                                x.chars().take(60).collect::<String>()
                            ),
                        )
                    })
                    .collect()
            },
        };
        squeezed.say(&format!("split into 3 parts\n{text}"));
        assert_eq!(
            squeezed.say("show part 2").reply,
            replies::SPLIT_NOT_IN_HISTORY
        );
        let mut merged = Chat {
            turns: Vec::new(),
            keep: |t| t[t.len().saturating_sub(2)..].to_vec(),
        };
        merged.say(&format!(
            "merge these\n{}\n{}",
            pyjson::dumps(&fixtures::part(1)),
            pyjson::dumps(&fixtures::part(2))
        ));
        merged.say("as json");
        assert_eq!(
            merged.say("show the merge notes").reply,
            replies::MERGE_NOT_IN_HISTORY
        );
        assert_eq!(Chat::new().say("show part 2").reply, replies::NO_SPLIT_YET);
    }
}
