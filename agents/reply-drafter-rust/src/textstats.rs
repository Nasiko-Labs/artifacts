//! Pure: the counting both the style metrics and the tone checks use, so a reply is measured exactly the
//! way the examples were.

use std::sync::LazyLock;

use crate::util::{Regex, find_iter, is_match, re, split};

pub const LOW_FACTOR: f64 = 0.5;
pub const HIGH_FACTOR: f64 = 1.6;

static WORD: LazyLock<Regex> = LazyLock::new(|| re(r"[A-Za-z0-9][A-Za-z0-9'’-]*"));
static CONTRACTION: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\b[A-Za-z]+(?:n['’]t|['’](?:ll|re|ve|d|m))\b"));
/// 's is a contraction only for these words; anything else ("Sam's") is read as a possessive.
static S_CONTRACTION: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)\b(?:it|that|what|there|here|let|he|she|who|where|how|when|everything|nothing)['’]s\b")
});
static SENTENCE_END: LazyLock<Regex> = LazyLock::new(|| re(r"[.!?]+(?=\s|$)|\n+"));
pub static EMOJI: LazyLock<Regex> = LazyLock::new(|| {
    re(r"[\x{1F000}-\x{1FAFF}\x{2600}-\x{27BF}\x{2B00}-\x{2BFF}\x{FE0F}\x{200D}]")
});
pub static BULLET: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^(\s*)(?:[-*•]|\d+[.)])\s+"));

pub fn word_count(text: &str) -> usize {
    find_iter(&WORD, text).len()
}

pub fn contractions(text: &str) -> usize {
    find_iter(&CONTRACTION, text).len() + find_iter(&S_CONTRACTION, text).len()
}

pub fn sentence_count(text: &str) -> usize {
    split(&SENTENCE_END, text)
        .into_iter()
        .filter(|p| is_match(&WORD, p))
        .count()
        .max(1)
}

pub fn has_emoji(text: &str) -> bool {
    is_match(&EMOJI, text)
}

pub fn has_bullets(text: &str) -> bool {
    is_match(&BULLET, text)
}

/// The accepted reply length around the examples' average: [0.5x, 1.6x].
pub fn length_bounds(avg_words: usize) -> (usize, usize) {
    let avg = avg_words as f64;
    (
        (LOW_FACTOR * avg).floor() as usize,
        (HIGH_FACTOR * avg).ceil() as usize,
    )
}

/// 0 when the examples have none; else max(1, ceil(rate x words / 100 x 1.5)).
pub fn allowed_exclamations(exclaim_rate: f64, words: usize) -> usize {
    if exclaim_rate <= 0.0 {
        return 0;
    }
    let allowed = (exclaim_rate * words as f64 / 100.0 * 1.5).ceil();
    (allowed.max(1.0)) as usize
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counts_match_the_python_agent() {
        assert_eq!(
            word_count("Hey Jo, can't do Fri, how about Mon? Cheers, A"),
            10
        );
        assert_eq!(contractions("it's Sam's, can't, we'll"), 3);
        assert_eq!(sentence_count("One. Two!\nThree"), 3);
        assert!(has_emoji("done 🎉") && !has_emoji("done"));
        assert!(has_bullets("x\n- one") && !has_bullets("a - b"));
        assert_eq!(length_bounds(8), (4, 13));
        assert_eq!(allowed_exclamations(0.0, 50), 0);
        assert_eq!(allowed_exclamations(1.0, 10), 1);
    }
}
