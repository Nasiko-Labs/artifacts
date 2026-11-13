//! Pure: does a follow-up answer mostly copy the previous reply instead of answering the new question?
//!
//! Measured as the share of the answer's word trigrams that also occur in the previous reply (its italic
//! note lines such as "_Analysis only…_" left out). At or above `RESTATE_OVERLAP` the answer is a restatement
//! and the follow-up call is retried once.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;

use crate::util::re;

/// Share of the answer's trigrams found in the previous reply at which it counts as a copy.
pub const RESTATE_OVERLAP: f64 = 0.5;
const NGRAM: usize = 3;

static WORD: LazyLock<Regex> = LazyLock::new(|| re(r"[A-Za-z0-9_]+"));

fn words(text: &str) -> Vec<String> {
    WORD.find_iter(text)
        .map(|m| m.as_str().to_lowercase())
        .collect()
}

fn trigrams(words: &[String]) -> Vec<String> {
    words.windows(NGRAM).map(|w| w.join(" ")).collect()
}

/// The share (0.0-1.0) of `answer`'s word trigrams that appear in `previous`; 0.0 when either is too short.
pub fn overlap(answer: &str, previous: &str) -> f64 {
    let prose: String = previous
        .lines()
        .filter(|line| !line.trim_start().starts_with('_'))
        .collect::<Vec<_>>()
        .join("\n");
    let seen: HashSet<String> = trigrams(&words(&prose)).into_iter().collect();
    let mine = trigrams(&words(answer));
    if mine.is_empty() || seen.is_empty() {
        return 0.0;
    }
    let shared = mine.iter().filter(|t| seen.contains(*t)).count();
    shared as f64 / mine.len() as f64
}

pub fn restates(answer: &str, previous: &str) -> bool {
    overlap(answer, previous) >= RESTATE_OVERLAP
}

#[cfg(test)]
mod tests {
    use super::{overlap, restates};

    const PREVIOUS: &str = "To fix the KeyError related to 'items', use order.get(\"items\", []) at line 42.\n\n_Analysis only: nothing was run or sent. Copy what helps._";

    #[test]
    fn a_copy_or_near_copy_of_the_previous_reply_restates_it() {
        assert!(restates(
            "To fix the KeyError related to 'items', use order.get(\"items\", []) at line 42.",
            PREVIOUS
        ));
        assert!(restates(
            "To fix the KeyError related to 'items', use order.get(\"items\", []) at line 42 of service.py.",
            PREVIOUS
        ));
    }

    #[test]
    fn a_new_answer_that_mentions_the_fix_does_not() {
        let answer = "Nothing differs on Windows: a missing key raises KeyError on every OS. The order.get default \
                      still applies, but check why the payload lacks items.";
        assert!(!restates(answer, PREVIOUS));
    }

    #[test]
    fn short_or_empty_texts_never_count() {
        assert_eq!(overlap("Yes.", PREVIOUS), 0.0);
        assert_eq!(overlap("To fix the KeyError", ""), 0.0);
    }
}
