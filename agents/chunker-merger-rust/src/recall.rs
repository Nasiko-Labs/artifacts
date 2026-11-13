//! Pure: what follow-ups work on, recovered from the platform's history slice (the agent stores nothing).
//!
//! A split is deterministic, so "show part 3", "list the parts", "as json" and "split again with 10,000
//! chars" recompute it from the newest user message that carried text to split, with that message's options
//! and the "split again" options after it replayed in order. A merge is recomputed from the user messages
//! that pasted its results ("merge these" and every "merge with this" after it). A source message the
//! platform compressed (it carries the "[⋯ " sigil) counts as lost; an older one is never used in its place.

use serde_json::Value;

use crate::classify::{Kind, Request, classify};
use crate::envelope::extract_values;
use crate::history::{Turn, USER};
use crate::split::SplitOptions;

const MIN_TEXT_CHARS: usize = 40;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum Last {
    #[default]
    Nothing,
    Split,
    Merge,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Recalled {
    pub source: String,
    pub split_opts: SplitOptions,
    pub merge_set: Vec<Value>,
    /// Which came last.
    pub last: Last,
    /// The newest split's text is compressed in the history.
    pub split_lost: bool,
    pub merge_lost: bool,
}

fn is_source(kind: Kind) -> bool {
    matches!(kind, Kind::Split | Kind::Append | Kind::End)
}

fn is_merge(kind: Kind) -> bool {
    matches!(kind, Kind::Merge | Kind::MergeMore)
}

fn requests(turns: &[Turn]) -> Vec<(&Turn, Request)> {
    let mut out: Vec<(&Turn, Request)> = Vec::new();
    for turn in turns.iter().filter(|t| t.role == USER) {
        let request = resolve(
            classify(&turn.text),
            &turn.text,
            out.last().map(|(t, r)| (*t, r)),
        );
        out.push((turn, request));
    }
    out
}

/// A plain paste (no cue) right after a split with no text, or a merge with fewer than two results, is that
/// request's missing input: the stateless stand-in for answering a question.
pub fn resolve(request: Request, text: &str, previous: Option<(&Turn, &Request)>) -> Request {
    let Some((turn, before)) = previous.filter(|_| request.kind == Kind::Decline) else {
        return request;
    };
    let pasted = text.trim();
    if before.kind == Kind::Split
        && before.payload.is_empty()
        && pasted.chars().count() >= MIN_TEXT_CHARS
    {
        return Request {
            opts: before.opts.clone(),
            ..Request::with_payload(Kind::Split, pasted)
        };
    }
    let merge_short =
        before.kind == Kind::Merge && extract_values(&before.payload).values.len() < 2;
    if merge_short && !turn.compressed() && !extract_values(text).values.is_empty() {
        return Request::with_payload(Kind::MergeMore, text);
    }
    request
}

pub fn recall(turns: &[Turn]) -> Recalled {
    let asked = requests(turns);
    let split_at = asked
        .iter()
        .rposition(|(_, r)| is_source(r.kind) && !r.payload.is_empty());
    let merge_at = asked.iter().rposition(|(_, r)| is_merge(r.kind));
    let last = match (split_at, merge_at) {
        (s, Some(m)) if s.is_none_or(|s| m > s) => Last::Merge,
        (Some(_), _) => Last::Split,
        _ => Last::Nothing,
    };
    let mut found = Recalled {
        last,
        ..Recalled::default()
    };
    if let Some(at) = split_at {
        found = with_split(found, &asked, at);
    }
    if let Some(at) = merge_at {
        found = with_merge(found, &asked, at);
    }
    found
}

fn with_split(found: Recalled, asked: &[(&Turn, Request)], at: usize) -> Recalled {
    let (turn, request) = &asked[at];
    if turn.compressed() {
        return Recalled {
            split_lost: true,
            ..found
        };
    }
    let opts = asked[at + 1..]
        .iter()
        .filter(|(_, later)| later.kind == Kind::Resplit)
        .fold(request.opts.clone(), |opts, (_, later)| {
            opts.overlay(&later.opts)
        });
    Recalled {
        source: request.payload.clone(),
        split_opts: opts,
        ..found
    }
}

fn with_merge(found: Recalled, asked: &[(&Turn, Request)], at: usize) -> Recalled {
    let mut values: Vec<Value> = Vec::new();
    for (turn, request) in asked[..=at].iter().rev() {
        if !is_merge(request.kind) {
            continue;
        }
        if turn.compressed() {
            return Recalled {
                merge_lost: true,
                ..found
            };
        }
        values.splice(0..0, extract_values(&request.payload).values);
        if request.kind == Kind::Merge {
            return Recalled {
                merge_set: values,
                ..found
            };
        }
    }
    Recalled {
        merge_lost: true,
        ..found
    } // "merge with this" whose first merge is gone
}

pub fn previous_request(turns: &[Turn]) -> Option<(Turn, Request)> {
    requests(turns)
        .pop()
        .map(|(turn, request)| (turn.clone(), request))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixtures;
    use crate::history;
    use crate::pyjson;

    #[test]
    fn empty_or_garbled_history_recalls_nothing() {
        let garbled = "[summary of earlier turns]\ngarbled ::: text\n\nCurrent message: x";
        for raw in [
            "",
            "no marker",
            "\n\nCurrent message: x",
            garbled,
            "user:\nassistant: \n\nCurrent message: x",
        ] {
            let turns = history::parse(history::split(raw).0);
            assert!(turns.is_empty());
            assert_eq!(recall(&turns), Recalled::default());
        }
    }

    #[test]
    fn takes_the_newest_source_replays_resplits_and_collects_merge_pastes() {
        let [a, b, c] = [1, 2, 3].map(|n| pyjson::dumps(&fixtures::part(n)));
        let block = format!(
            "user: split into 2 parts\nold text\nassistant: x\nuser: split into 3 parts\nnew text\nassistant: y\nuser: split again with 5,000 chars\nassistant: z\nuser: merge these\n{a}\n{b}\nassistant: m\nuser: merge with this one\n{c}\nassistant: n"
        );
        let found = recall(&history::parse(&block));
        assert_eq!(found.source, "new text");
        assert_eq!(
            (found.split_opts.size, found.split_opts.parts),
            (Some(5_000), None)
        );
        assert_eq!((found.merge_set.len(), found.last), (3, Last::Merge));
    }

    #[test]
    fn history_splits_on_the_last_marker_and_keeps_multiline_turns() {
        let (block, current) = history::split(
            "user: split into 2 parts\nline a\n\nline b\nassistant: ok\n\nCurrent message: x",
        );
        assert_eq!(current, "x");
        assert_eq!(
            history::parse(block)[0],
            Turn::new("user", "split into 2 parts\nline a\n\nline b")
        );
    }
}
