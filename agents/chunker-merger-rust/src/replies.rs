//! Fixed replies. There is no model: every reply that is not a split or a merge is one of these.

use std::sync::LazyLock;

use crate::smalltalk::INTRO;

pub static OFF_TOPIC_REPLY: LazyLock<String> = LazyLock::new(|| {
    format!(
        "That's outside what I do: I only split text and merge results, so I can't help with it. {}",
        *INTRO
    )
});
pub const NEED_TEXT: &str = "There's no text to split in that message. Paste the text in your next message and I'll split it \
with the options you gave.";
pub const NEED_ENVELOPES: &str = "I need at least two JSON results to merge. Paste the other results (fenced json blocks, a \
JSON array, or objects separated by ---) in your next message and I'll merge them with this one.";
pub const DIFFERENT_AGENTS: &str = "These look like results from different agents; I merge parts of one run. Paste results \
that share the same fields.";
pub const PIECE_NOTE: &str = "I keep nothing between messages, so I split each piece as it arrives (parts are numbered within \
this piece). To split the whole text as one, send it in one message or as a file.";
pub const NO_SPLIT_YET: &str =
    "There's no split in this conversation yet. Paste a text and say how to split it.";
pub const NO_MERGE_YET: &str =
    "There's no merge in this conversation yet. Paste two or more JSON results with 'merge these'.";
pub const SPLIT_NOT_IN_HISTORY: &str = "I can't see the text you asked me to split in the recent conversation any more (long \
pastes get trimmed), so please paste it again with how to split it.";
pub const MERGE_NOT_IN_HISTORY: &str = "I can't see the results from the earlier merge in the recent conversation any more (long \
pastes get trimmed), so please paste all the results again with 'merge these'.";
pub const NEED_TEXT_WITH_PART: &str =
    "Paste this piece's text after the first line (\"part 1 of 3, more coming\" then the text).";
pub const NOTHING_HELD: &str = "I keep nothing between messages: each piece was split when it arrived. Paste text to split \
it.";
pub const TOKEN_NOTE: &str = "Token counts are estimates (characters / 4); real counts vary, especially for non-English text.";

pub fn over_cap_cut(total: usize, kept: usize) -> String {
    format!(
        "This text is {} characters; I split the first {} and the last {} were left out. Send the rest in another \
message to split it too.",
        thousands(total as u64),
        thousands(kept as u64),
        thousands(total.saturating_sub(kept) as u64)
    )
}

pub fn no_such_part(n: u64, total: usize) -> String {
    format!("There's no part {n}: the last split has {total} part(s).")
}

/// `100000` -> `100,000`.
pub fn thousands(n: u64) -> String {
    let digits = n.to_string();
    let mut out = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(ch);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn thousands_separators() {
        assert_eq!(thousands(7), "7");
        assert_eq!(thousands(600_000), "600,000");
        assert_eq!(thousands(1_234_567), "1,234,567");
    }
}
