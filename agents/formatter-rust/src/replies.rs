//! Fixed replies. There is no model: every reply that is not a rendering is one of these.

use std::sync::LazyLock;

use crate::smalltalk::INTRO;

pub static OFF_TOPIC_REPLY: LazyLock<String> = LazyLock::new(|| {
    format!(
        "That's outside what I do, so I can't help with it here. {}",
        *INTRO
    )
});
pub static NO_JSON: LazyLock<String> = LazyLock::new(|| {
    format!(
        "There's nothing to format yet: paste the JSON (or the Markdown) in the same message. {}",
        *INTRO
    )
});
pub const NOT_IN_HISTORY: &str = "I can't see the JSON (or Markdown) you pasted earlier in the recent conversation any more \
(long pastes get trimmed), so please paste it again with what you want, for example \"as slack: <json>\".";
pub const NOT_FAITHFUL_ANY: &str =
    "Could not render this without changing content, so here is the JSON as given.";

pub fn too_big(chars: usize, limit: usize) -> String {
    format!(
        "That's {} characters; I format up to {} characters of JSON per message. Paste a smaller part, or one \
envelope at a time.",
        thousands(chars),
        thousands(limit)
    )
}

pub fn json_error(error: &str) -> String {
    format!(
        "That JSON didn't parse ({error}). Fix it and paste it again; I format exactly what you give me, so I \
don't guess at broken JSON."
    )
}

pub fn not_faithful(target_label: &str) -> String {
    format!(
        "Could not render as {target_label} without changing content, so here it is as Markdown."
    )
}

/// `100000` -> `100,000`.
pub fn thousands(n: usize) -> String {
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
        assert_eq!(thousands(100_000), "100,000");
        assert_eq!(thousands(1_234_567), "1,234,567");
    }
}
