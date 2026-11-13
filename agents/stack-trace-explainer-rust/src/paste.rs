//! Pure: one pasted message into traces and code blocks.
//!
//! Code blocks are found first: a fenced block (unless it holds a trace), or an unfenced block under a
//! header line (`# file: app/orders.py`, `// file: src/a.ts`, `--- app/orders.py`). A block may declare
//! its first line number (`# lines 40-70`). What is left is split into traces at start markers; only
//! the last 3 traces are kept, and the last one is primary unless the user's words point at the first.

use std::sync::LazyLock;

use regex::{Captures, Regex};

use crate::util::{char_len, group, lines, num, re, take_chars, thousands};

pub const MAX_TRACES: usize = 3;
pub const MAX_BLOCK_CHARS: usize = 12_000;
pub const MAX_CODE_CHARS: usize = 40_000;
pub const LOOSE_MIN_LINES: usize = 3;

static FENCE: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?ms)^```[ \t]*(?P<info>[^\n`]*)\n(?P<body>.*?)^```[ \t]*$"));
static HEADER: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)^\s*(?:(?:#|//|--)\s*file:\s*(?P<a>\S+)|--- (?:a/)?(?P<b>[\w./\\-]+\.\w+))[^\n]*$")
});
pub static LINES_RANGE: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\blines?\s+(?P<start>[0-9]+)\s*(?:-|–|to)\s*(?P<end>[0-9]+)"));
static OFFSET_LINE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)^\s*(?:#|//|--|/\*)?\s*lines?\s+[0-9]+\s*(?:-|–|to)\s*[0-9]+\s*(?:\*/)?\s*$")
});
pub static PATH: LazyLock<Regex> = LazyLock::new(|| {
    re(r"[\w./\\-]*\w\.(?:py|js|mjs|cjs|ts|tsx|jsx|java|kt|go|rs|rb|cs|php|scala|swift)\b")
});
static CHAIN: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"During handling of the above exception|The above exception was the direct cause|Caused by:",
    )
});
static STARTS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
        r"^\s*Traceback \(most recent call last\):",
        r"^Exception in thread ",
        r"^(?:panic|fatal error): ",
        r"^thread '[^']*' panicked at",
        r"^Unhandled exception\.",
        r"^\S+:[0-9]+:in [`'][^'`]+'",
    ]
    .iter()
    .map(|p| re(p))
    .collect()
});
static GOROUTINE: LazyLock<Regex> = LazyLock::new(|| re(r"^goroutine [0-9]+ \["));
static GO_SEGMENT_HEAD: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(?:panic|fatal error): |^goroutine "));
static HEAD_WITH_AT: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(?:Uncaught )?[\w.$]*(?:Error|Exception)\b"));
static AT_LINE: LazyLock<Regex> = LazyLock::new(|| re(r"^\s+at \S"));
static FIRST: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\bfirst (?:error|one|trace|exception|stack ?trace|traceback)\b"));
static CODEISH: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"[(){};=\[\]]|^\s{2,}\S|^\s*(?:def|class|return|if|for|while|import|from|func|fn|let|const|",
        r"var|public|private|static|async|await)\b"
    ))
});

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CodeBlock {
    /// "" when nothing labels it.
    pub path: String,
    pub lines: Vec<String>,
    pub start: i64,
    pub explicit_start: bool,
    pub truncated: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Paste {
    /// The user's own words outside traces and code (the request).
    pub words: String,
    /// Kept traces, oldest first.
    pub traces: Vec<String>,
    pub blocks: Vec<CodeBlock>,
    /// Index into traces.
    pub primary: usize,
    pub skipped_traces: usize,
    pub notes: Vec<String>,
}

pub fn split(text: &str) -> Paste {
    let (rest, blocks, notes) = code_blocks(text);
    let (words, traces) = split_traces(&rest);
    let skipped = traces.len().saturating_sub(MAX_TRACES);
    let kept: Vec<String> = traces.into_iter().skip(skipped).collect();
    let primary = if FIRST.is_match(&words) {
        0
    } else {
        kept.len().saturating_sub(1)
    };
    Paste {
        words: words.trim().to_string(),
        traces: kept,
        blocks,
        primary,
        skipped_traces: skipped,
        notes,
    }
}

type RawBlock = (String, Vec<String>, String); // (path, lines, header line)

fn code_blocks(text: &str) -> (String, Vec<CodeBlock>, Vec<String>) {
    let mut raw: Vec<RawBlock> = Vec::new();
    let rest = FENCE
        .replace_all(text, |caps: &Captures| take_fence(text, caps, &mut raw))
        .into_owned();
    let lines = lines(&rest);
    let mut rest_lines: Vec<&str> = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        let Some(header) = HEADER.captures(lines[i]) else {
            rest_lines.push(lines[i]);
            i += 1;
            continue;
        };
        let mut j = i + 1;
        while j < lines.len() && !HEADER.is_match(lines[j]) && !is_start(&lines, j) {
            j += 1;
        }
        if lines[i + 1..j].iter().any(|l| !l.trim().is_empty()) {
            let path = first_non_empty(group(&header, "a"), group(&header, "b"));
            let body = lines[i + 1..j].iter().map(|l| l.to_string()).collect();
            raw.push((path.to_string(), body, lines[i].to_string()));
        }
        i = j;
    }
    let (blocks, notes) = finish(raw);
    (rest_lines.join("\n"), blocks, notes)
}

fn first_non_empty<'a>(a: &'a str, b: &'a str) -> &'a str {
    if a.is_empty() { b } else { a }
}

fn take_fence(text: &str, caps: &Captures, raw: &mut Vec<RawBlock>) -> String {
    let body = group(caps, "body");
    let has_start = lines(body)
        .iter()
        .any(|l| STARTS.iter().any(|rx| rx.is_match(l)));
    if has_start || looks_like_trace(body) {
        return body.to_string();
    }
    let start = caps.get(0).map_or(0, |m| m.start());
    let before = lines(text[..start].trim_end());
    let previous = before.last().copied().unwrap_or("");
    let header_path = HEADER
        .captures(previous)
        .map(|h| first_non_empty(group(&h, "a"), group(&h, "b")).to_string())
        .unwrap_or_default();
    let mut path = path_in(group(caps, "info"));
    if path.is_empty() {
        path = header_path;
    }
    if path.is_empty() {
        path = path_in(previous);
    }
    let body_lines = lines(body.trim_end_matches('\n'))
        .iter()
        .map(|l| l.to_string())
        .collect();
    raw.push((path, body_lines, previous.to_string()));
    String::new()
}

fn finish(raw: Vec<RawBlock>) -> (Vec<CodeBlock>, Vec<String>) {
    let mut blocks = Vec::new();
    let mut notes = Vec::new();
    let mut total = 0usize;
    for (path, mut lines_, header) in raw {
        let mut offset = LINES_RANGE
            .captures(&header)
            .map(|c| group(&c, "start").to_string());
        if lines_.first().is_some_and(|l| OFFSET_LINE.is_match(l)) {
            offset = LINES_RANGE
                .captures(&lines_[0])
                .map(|c| group(&c, "start").to_string());
            lines_.remove(0);
        }
        let (start, explicit) = match offset {
            Some(start) => (num(&start).max(1), true),
            None => (1, false),
        };
        while lines_.last().is_some_and(|l| l.trim().is_empty()) {
            lines_.pop();
        }
        let body = lines_.join("\n");
        let size = char_len(&body);
        let truncated = size > MAX_BLOCK_CHARS;
        let label = if path.is_empty() {
            "an unlabelled snippet"
        } else {
            path.as_str()
        };
        if total + size.min(MAX_BLOCK_CHARS) > MAX_CODE_CHARS {
            notes.push(format!(
                "Skipped the code block for {label}: over the {}-character code limit.",
                thousands(MAX_CODE_CHARS)
            ));
            continue;
        }
        if truncated {
            lines_ = lines(take_chars(&body, MAX_BLOCK_CHARS))
                .iter()
                .map(|l| l.to_string())
                .collect();
            let name = if path.is_empty() {
                "a snippet"
            } else {
                path.as_str()
            };
            notes.push(format!(
                "Read only the first {} characters of {name}.",
                thousands(MAX_BLOCK_CHARS)
            ));
        }
        total += size.min(MAX_BLOCK_CHARS);
        blocks.push(CodeBlock {
            path,
            lines: lines_,
            start,
            explicit_start: explicit,
            truncated,
        });
    }
    (blocks, notes)
}

pub fn path_in(text: &str) -> String {
    PATH.find(text)
        .map_or(String::new(), |m| m.as_str().to_string())
}

fn looks_like_trace(body: &str) -> bool {
    let lines = lines(body);
    lines.iter().any(|l| HEAD_WITH_AT.is_match(l)) && lines.iter().any(|l| AT_LINE.is_match(l))
}

fn is_start(lines: &[&str], i: usize) -> bool {
    let line = lines[i];
    if STARTS.iter().any(|rx| rx.is_match(line)) {
        return true;
    }
    if HEAD_WITH_AT.is_match(line) {
        let following = lines[i + 1..]
            .iter()
            .find(|l| !l.trim().is_empty())
            .copied()
            .unwrap_or("");
        return AT_LINE.is_match(following);
    }
    false
}

fn split_traces(text: &str) -> (String, Vec<String>) {
    let lines = lines(text);
    let mut segments: Vec<Vec<&str>> = Vec::new();
    let mut words: Vec<&str> = Vec::new();
    for i in 0..lines.len() {
        if starts_segment(&lines, i, &segments) {
            segments.push(vec![lines[i]]);
        } else if let Some(last) = segments.last_mut() {
            last.push(lines[i]);
        } else {
            words.push(lines[i]);
        }
    }
    if segments.is_empty() {
        let traces = if text.trim().is_empty() {
            Vec::new()
        } else {
            vec![text.to_string()]
        };
        return (text.to_string(), traces);
    }
    let traces = segments
        .iter()
        .map(|s| s.join("\n").trim().to_string())
        .collect();
    (words.join("\n"), traces)
}

fn starts_segment(lines: &[&str], i: usize, segments: &[Vec<&str>]) -> bool {
    let line = lines[i];
    if GOROUTINE.is_match(line) {
        let current_head = segments.last().and_then(|s| s.first()).copied();
        return !current_head.is_some_and(|head| GO_SEGMENT_HEAD.is_match(head));
    }
    if !is_start(lines, i) {
        return false;
    }
    let previous: Vec<&str> = lines[i.saturating_sub(3)..i]
        .iter()
        .filter(|l| !l.trim().is_empty())
        .copied()
        .collect();
    // A chained traceback continues the same trace.
    !previous.last().is_some_and(|l| CHAIN.is_match(l))
}

/// Unfenced, unlabelled code pasted as a follow-up ("lines 40-70 of OrderRepository.java:" + code): a
/// block labelled from the prose line before it, or None when the message does not look like code.
pub fn loose_block(text: &str) -> Option<CodeBlock> {
    let all = lines(text);
    let first = all.iter().position(|l| !l.trim().is_empty())?;
    let lines_ = &all[first..];
    let head = lines_[0];
    let is_label = !CODEISH.is_match(head) || (PATH.is_match(head) && LINES_RANGE.is_match(head));
    let label = if is_label { head } else { "" };
    let body: &[&str] = if is_label { &lines_[1..] } else { lines_ };
    let code_lines: Vec<&&str> = body.iter().filter(|l| !l.trim().is_empty()).collect();
    let codeish = code_lines.iter().filter(|l| CODEISH.is_match(l)).count();
    if code_lines.len() < LOOSE_MIN_LINES || codeish * 2 < code_lines.len() {
        return None;
    }
    let offset = LINES_RANGE.captures(label);
    let start = offset.as_ref().map_or(1, |c| num(group(c, "start")).max(1));
    Some(CodeBlock {
        path: path_in(label),
        lines: body
            .iter()
            .take(MAX_BLOCK_CHARS)
            .map(|l| l.to_string())
            .collect(),
        start,
        explicit_start: offset.is_some(),
        truncated: false,
    })
}
