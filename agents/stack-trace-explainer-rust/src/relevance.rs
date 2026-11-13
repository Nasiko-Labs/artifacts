//! Pure: is a message after an explanation clearly about that error?
//!
//! A trace in the history does not make every later message a follow-up: "what's 2+2?" is unrelated
//! and must be declined. A question that uses error vocabulary ("fix", "why", "line") or names
//! something from the trace (`KeyError`, `get_order`, `service.py`) or from the last answer (a term of
//! the explanation or fix, e.g. `defaultdict`) is clearly a follow-up and goes straight to the
//! follow-up call. Anything else is unclear and gets one cheap relatedness check first, which leans
//! towards "related" and declines only what is clearly unrelated.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;

use crate::util::re;

/// Words that only make sense as a question about an error, its cause, fix or verification.
const ERROR_WORDS: &[&str] = &[
    "fix",
    "fixed",
    "fixing",
    "fixes",
    "error",
    "errors",
    "exception",
    "exceptions",
    "crash",
    "crashes",
    "crashed",
    "crashing",
    "bug",
    "bugs",
    "trace",
    "traceback",
    "stack",
    "line",
    "lines",
    "cause",
    "caused",
    "causes",
    "causing",
    "why",
    "verify",
    "test",
    "tests",
    "testing",
    "repro",
    "reproduce",
    "debug",
    "debugging",
    "happen",
    "happens",
    "happened",
    "happening",
    "mean",
    "means",
    "meaning",
    "explain",
    "explanation",
    "code",
    "function",
    "method",
    "file",
    "frame",
    "fail",
    "fails",
    "failed",
    "failing",
    "failure",
    "solve",
    "solution",
    "prevent",
    "avoid",
    "workaround",
    "patch",
    "change",
    "wrong",
    "broken",
    "issue",
    "problem",
    "null",
    "none",
    "nil",
    "undefined",
    "missing",
    "throw",
    "throws",
    "thrown",
    "raise",
    "raises",
    "raised",
    "handle",
    "catch",
    "confidence",
    "sure",
    "certain",
    "elaborate",
    "detail",
    "details",
    "example",
];

/// Short common words that never tie a question to a trace on their own.
const STOP_WORDS: &[&str] = &[
    "the", "and", "for", "what", "whats", "how", "who", "with", "from", "this", "that", "are",
    "you", "can", "does", "did", "was", "were", "has", "have", "its", "into", "your", "about",
    "most", "recent", "call", "last", "not", "but", "all", "any", "out",
];

const MIN_TOKEN_CHARS: usize = 3;
/// Terms taken from the last answer must be at least this long: its prose has many short common words.
const MIN_REPLY_TERM_CHARS: usize = 5;

static TOKEN: LazyLock<Regex> = LazyLock::new(|| re(r"[A-Za-z_][A-Za-z0-9_]*"));

fn tokens(text: &str) -> impl Iterator<Item = String> + '_ {
    TOKEN
        .find_iter(text)
        .map(|m| m.as_str().to_lowercase())
        .filter(|t| t.chars().count() >= MIN_TOKEN_CHARS || ERROR_WORDS.contains(&t.as_str()))
}

/// True when `question` uses error vocabulary or names a term from `trace` or from `last_reply`
/// (the last explanation or fix; its italic note lines such as "_Analysis only…_" are skipped).
pub fn clearly_about(question: &str, trace: &str, last_reply: &str) -> bool {
    let mut terms: HashSet<String> = tokens(trace)
        .filter(|t| !STOP_WORDS.contains(&t.as_str()))
        .collect();
    let answer_lines = last_reply
        .lines()
        .filter(|line| !line.trim_start().starts_with('_'));
    for line in answer_lines {
        terms.extend(tokens(line).filter(|t| {
            t.chars().count() >= MIN_REPLY_TERM_CHARS && !STOP_WORDS.contains(&t.as_str())
        }));
    }
    tokens(question).any(|t| {
        ERROR_WORDS.contains(&t.as_str())
            || (!STOP_WORDS.contains(&t.as_str()) && terms.contains(&t))
    })
}

#[cfg(test)]
mod tests {
    use super::clearly_about;

    const TRACE: &str = "Traceback (most recent call last):\n  File \"/app/orders/service.py\", line 42, in get_order\n    total = order[\"items\"][0][\"price\"] * qty\nKeyError: 'items'";
    const REPLY: &str = "**Fix.** Use a defaultdict or order.get(\"items\", []) before indexing.\n\n_Analysis only: nothing was run or sent. Copy what helps._";

    #[test]
    fn error_words_and_trace_names_are_clearly_about_the_error() {
        for q in [
            "How do I fix it?",
            "why?",
            "what does KeyError mean?",
            "is get_order the right place?",
            "what about service.py",
        ] {
            assert!(clearly_about(q, TRACE, ""), "{q}");
        }
    }

    #[test]
    fn unrelated_questions_are_not_clearly_about_the_error() {
        for q in [
            "what's 2+2?",
            "what is the capital of France?",
            "tell me a joke",
            "",
        ] {
            assert!(!clearly_about(q, TRACE, REPLY), "{q}");
        }
    }

    #[test]
    fn terms_from_the_last_answer_tie_a_question_to_the_error() {
        assert!(clearly_about(
            "would a defaultdict be better?",
            TRACE,
            REPLY
        ));
        assert!(clearly_about("why line 42?", TRACE, REPLY));
        assert!(!clearly_about("would a defaultdict be better?", TRACE, ""));
    }

    #[test]
    fn note_lines_of_the_last_answer_are_not_terms() {
        assert!(!clearly_about("copy what?", TRACE, REPLY));
    }
}
