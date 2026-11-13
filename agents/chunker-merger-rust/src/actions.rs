//! Pure: the operations behind a turn (split, a piece of a longer text, merge). Each returns this turn's
//! reply and envelope. Stateless: nothing is held between messages and nothing ever pauses; a missing input
//! or a too-long text gets a plain reply (see PLAN.md).

use serde_json::Value;

use crate::envelope::Pasted;
use crate::merge::{Status, merge};
use crate::render;
use crate::replies;
use crate::split::{SplitOptions, split_text};

/// One message's text is capped here (cut back to a line break) and the rest is named as left out.
pub const CAP_CHARS: usize = 600_000;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Out {
    pub reply: String,
    pub envelope: Option<Value>,
}

impl Out {
    pub fn reply(text: impl Into<String>) -> Self {
        Self {
            reply: text.into(),
            envelope: None,
        }
    }
}

/// At most `cap` characters of `text`, cut back to the last line break in reach.
pub fn cap_cut(text: &str, cap: usize) -> &str {
    let head = match text.char_indices().nth(cap) {
        Some((byte, _)) => &text[..byte],
        None => return text,
    };
    match head.rfind('\n') {
        Some(cut) if head[..cut].chars().count() > cap / 2 => &head[..cut],
        _ => head,
    }
}

pub fn split(text: &str, opts: &SplitOptions, notes: &[String]) -> Result<Out, String> {
    split_capped(text, opts, notes, CAP_CHARS)
}

/// Over the cap: the first part is split and the reply says how much was left out.
pub fn split_capped(
    text: &str,
    opts: &SplitOptions,
    notes: &[String],
    cap: usize,
) -> Result<Out, String> {
    let total = text.chars().count();
    if total > cap {
        let kept = cap_cut(text, cap);
        let mut notes = notes.to_vec();
        notes.push(replies::over_cap_cut(total, kept.chars().count()));
        return split_capped(kept, opts, &notes, cap);
    }
    let result = split_text(text, opts)?;
    let reply = std::iter::once(render::split_reply(&result))
        .chain(notes.iter().cloned())
        .collect::<Vec<_>>()
        .join("\n\n");
    Ok(Out {
        reply,
        envelope: Some(result.envelope()),
    })
}

pub fn split_request(payload: &str, opts: &SplitOptions) -> Result<Out, String> {
    if payload.is_empty() {
        Ok(Out::reply(replies::NEED_TEXT))
    } else {
        split(payload, opts, &[])
    }
}

/// One piece of a text sent over several messages: split as it arrives (nothing is held).
pub fn piece(payload: &str, opts: &SplitOptions, last: bool) -> Result<Out, String> {
    if payload.is_empty() {
        return Ok(Out::reply(if last {
            replies::NOTHING_HELD
        } else {
            replies::NEED_TEXT_WITH_PART
        }));
    }
    split(payload, opts, &[replies::PIECE_NOTE.to_owned()])
}

pub fn merge_values(pasted: &Pasted, base: &[Value]) -> Out {
    let values: Vec<Value> = base.iter().chain(&pasted.values).cloned().collect();
    let result = merge(&values, &pasted.problems);
    match result.status {
        Status::NeedMore => {
            let text = result
                .notes
                .iter()
                .cloned()
                .chain([replies::NEED_ENVELOPES.to_owned()])
                .collect::<Vec<_>>();
            Out::reply(text.join("\n\n"))
        }
        Status::Different => {
            let text = std::iter::once(replies::DIFFERENT_AGENTS.to_owned())
                .chain(result.notes.iter().cloned());
            Out::reply(text.collect::<Vec<_>>().join("\n\n"))
        }
        Status::Ok => Out {
            reply: render::merge_reply(&result),
            envelope: result.envelope.clone(),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cap_cut_backs_up_to_a_line_break() {
        let text = format!("{}\n{}", "a".repeat(80), "b".repeat(80));
        assert_eq!(cap_cut(&text, 100), "a".repeat(80));
        assert_eq!(cap_cut(&"c".repeat(150), 100), "c".repeat(100));
        assert_eq!(cap_cut("short", 100), "short");
    }
}
