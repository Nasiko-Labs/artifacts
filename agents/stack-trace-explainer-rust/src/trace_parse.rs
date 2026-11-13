//! Pure: one trace's text into a `Trace`. The languages are tried in a fixed detection order; the first
//! parser that finds frames wins. Text with no frame at all is `frameless`: only its error line is parsed.

use std::sync::LazyLock;

use regex::Regex;

use crate::frames;
use crate::parse_jvm;
use crate::parse_other;
use crate::parse_python;
use crate::trace_model::{Block, Trace};
use crate::util::{group, lines, re, take_chars};

pub const ERROR_LINE_MAX_CHARS: usize = 300;

type Detect = fn(&str) -> bool;
type Parse = fn(&str) -> Option<Trace>;

const PARSERS: &[(Detect, Parse)] = &[
    (parse_python::detect, parse_python::parse),
    (parse_other::detect_go, parse_other::parse_go),
    (parse_other::detect_rust, parse_other::parse_rust),
    (parse_jvm::detect_csharp, parse_jvm::parse_csharp),
    (parse_jvm::detect_java, parse_jvm::parse_java),
    (parse_other::detect_ruby, parse_other::parse_ruby),
    (parse_other::detect_node, parse_other::parse_node),
];

static ERROR_WORDS: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)\b(?:\w*errors?|\w*exceptions?|traceback|panic(?:ked)?|fatal|failed|failure|refused|denied|segmentation fault|",
        r"segfault|stack ?trace|undefined|null ?pointer|nil pointer|cannot|can't find|not found|timed? ?out|",
        r"exit (?:code|status) [1-9][0-9]*|core dumped|unhandled|uncaught)\b"
    ))
});
static TYPED_LINE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?P<type>[A-Za-z_][\w.$]*(?:Error|Exception|Panic|Fault))\b:?\s*(?P<message>.*)")
});
static ERRNO: LazyLock<Regex> = LazyLock::new(|| re(r"\b(?P<type>E[A-Z]{3,})\b"));

pub fn has_error_text(text: &str) -> bool {
    ERROR_WORDS.is_match(text) || ERRNO.is_match(text)
}

/// A trace copied out of a JSON log has literal `\n` / `\t`: turn them into real ones, but only when
/// the text has no real line breaks of its own.
pub fn unescape(text: &str) -> String {
    if !text.trim().contains('\n') && text.contains("\\n") {
        return text
            .replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\\"", "\"");
    }
    text.to_string()
}

/// Always returns a trace: a parsed one with frames, else a frameless one built from the error line.
pub fn parse(text: &str, trust_libraries: bool) -> Trace {
    let body = unescape(text);
    for (detect, run) in PARSERS {
        if !detect(&body) {
            continue;
        }
        if let Some(trace) = run(&body).filter(|t| !t.all_frames().is_empty()) {
            return frames::classify(trace, trust_libraries);
        }
    }
    frameless(&body)
}

pub fn frameless(text: &str) -> Trace {
    let (exc_type, message) = error_line(text);
    let mut trace = Trace::new(
        "unknown",
        vec![Block::new(&exc_type, &message, Vec::new())],
        text,
    );
    trace.frameless = true;
    trace
}

/// (type, message) of the first line that looks like an error; ("Error", first line) otherwise.
pub fn error_line(text: &str) -> (String, String) {
    let lines: Vec<&str> = lines(text)
        .into_iter()
        .filter(|l| !l.trim().is_empty())
        .map(|l| l.trim().trim_matches('`'))
        .collect();
    let cut = |s: &str| take_chars(s, ERROR_LINE_MAX_CHARS).to_string();
    for line in &lines {
        if let Some(typed) = TYPED_LINE.captures(line) {
            return (
                group(&typed, "type").to_string(),
                cut(group(&typed, "message").trim()),
            );
        }
    }
    for line in &lines {
        if let Some(errno) = ERRNO.captures(line) {
            return (group(&errno, "type").to_string(), cut(line));
        }
    }
    for line in &lines {
        if has_error_text(line) {
            return ("Error".to_string(), cut(line));
        }
    }
    (
        "Error".to_string(),
        lines.first().map_or(String::new(), |l| cut(l)),
    )
}

/// A line like `KeyError: 'x'` / `ECONNREFUSED`: a frameless error the user pasted, not a question.
pub fn has_typed_error(text: &str) -> bool {
    lines(text)
        .iter()
        .any(|line| TYPED_LINE.is_match(line) || ERRNO.is_match(line))
}
