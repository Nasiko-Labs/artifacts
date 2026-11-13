//! Pure: Go, Rust, Ruby and Node/JavaScript traces. All four print the innermost frame first.

use std::sync::LazyLock;

use regex::Regex;

use crate::trace_model::{Block, Frame, Trace};
use crate::util::{group, lines, lstrip_chars, num, re};

// Go: `panic: msg` / `fatal error: msg`, then `goroutine N [running]:` and pairs of
// `pkg.Func(args)` + `\t/path:line`.
static GO_SIGNATURE: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?m)^(?:panic: |fatal error: |goroutine [0-9]+ \[)"));
static GO_HEAD: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(?P<type>panic|fatal error): (?P<message>.*)$"));
static GO_GOROUTINE: LazyLock<Regex> = LazyLock::new(|| re(r"^goroutine [0-9]+ \["));
static GO_LOCATION: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\s+(?P<file>\S+\.(?:go|s)):(?P<line>[0-9]+)(?: \+0x[0-9a-f]+)?\s*$"));
static GO_IN_GOROUTINE: LazyLock<Regex> = LazyLock::new(|| re(r" in goroutine [0-9]+$"));

// Rust: `thread 'main' panicked at src/main.rs:14:22:` + message line (1.73+), or the older quoted form.
static RUST_SIGNATURE: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^thread '[^']*' panicked at"));
static RUST_NEW: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^thread '[^']*' panicked at (?P<file>[^\s:]+):(?P<line>[0-9]+):(?P<col>[0-9]+):\s*$")
});
static RUST_OLD: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"^thread '[^']*' panicked at '(?P<message>.*)', (?P<file>[^\s:]+):(?P<line>[0-9]+):(?P<col>[0-9]+)",
    )
});
static RUST_FUNC: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*[0-9]+: (?P<func>\S.*)$"));
static RUST_AT: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\s*at (?P<file>.+):(?P<line>[0-9]+):(?P<col>[0-9]+)\s*$"));

// Ruby: `app/x.rb:12:in 'total': message (Type)` then `\tfrom file:line:in 'caller'`.
static RUBY_FRAME: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(?P<file>[^:\s]+):(?P<line>[0-9]+):in [`'](?P<func>[^'`]+)'"));
static RUBY_SIGNATURE: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^\S+:[0-9]+:in [`'][^'`]+'"));
static RUBY_TYPE: LazyLock<Regex> = LazyLock::new(|| re(r"\((?P<type>[A-Z][\w:]*)\)\s*$"));

// Node: `TypeError: msg` then `    at fn (/path/file.js:14:22)`; `[cause]: Error: msg` starts a chained block.
static NODE_FRAME: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"^\s*at (?:async )?(?:(?P<func>.+?) \()?(?P<file>[^()\s]+?):(?P<line>[0-9]+):(?P<col>[0-9]+)\)?\s*$",
    )
});
static NODE_SIGNATURE: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?m)^\s*at (?:async )?(?:.+? \()?[^()\s]+?:[0-9]+:[0-9]+\)?\s*$"));
static NODE_HEAD: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"^(?:Uncaught )?(?P<type>[A-Z]?[\w.$]*(?:Error|Exception|Rejection)\b[\w.$]*)",
        r"(?: \[(?P<code>[A-Z_]+)\])?(?::\s?(?P<message>.*))?$"
    ))
});
static NODE_CAUSE: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*\[cause\]:\s*(?P<rest>.*)$"));

pub fn detect_go(text: &str) -> bool {
    GO_SIGNATURE.is_match(text)
}

pub fn parse_go(text: &str) -> Option<Trace> {
    let lines = lines(text);
    let (mut exc_type, mut message) = ("panic".to_string(), String::new());
    let mut frames = Vec::new();
    let (mut in_goroutine, mut seen_goroutine) = (false, false);
    for (i, line) in lines.iter().enumerate() {
        if let Some(head) = GO_HEAD.captures(line).filter(|_| message.is_empty()) {
            exc_type = group(&head, "type").to_string();
            message = group(&head, "message").trim().to_string();
        }
        if GO_GOROUTINE.is_match(line) {
            in_goroutine = !seen_goroutine; // only the first goroutine (the one that panicked)
            seen_goroutine = true;
            continue;
        }
        if let Some(location) = GO_LOCATION.captures(line).filter(|_| in_goroutine && i > 0) {
            let func = go_func(lines[i - 1]);
            frames.push(Frame::new(
                group(&location, "file"),
                num(group(&location, "line")),
                &func,
            ));
        }
    }
    if message.is_empty() && frames.is_empty() {
        return None;
    }
    Some(Trace::new(
        "go",
        vec![Block::new(&exc_type, &message, frames)],
        text,
    ))
}

fn go_func(line: &str) -> String {
    let text = line.trim();
    let text = text.strip_prefix("created by ").unwrap_or(text);
    let text = GO_IN_GOROUTINE.replace(text, "").into_owned();
    match text.rfind('(') {
        Some(idx) if text.ends_with(')') => text[..idx].to_string(),
        _ => text,
    }
}

pub fn detect_rust(text: &str) -> bool {
    RUST_SIGNATURE.is_match(text)
}

pub fn parse_rust(text: &str) -> Option<Trace> {
    let lines = lines(text);
    let mut message = String::new();
    let mut panic_frame: Option<Frame> = None;
    let mut frames: Vec<Frame> = Vec::new();
    let mut func = String::new();
    for (i, line) in lines.iter().enumerate() {
        let new = RUST_NEW.captures(line);
        let old = RUST_OLD.captures(line);
        if let Some(caps) = new.as_ref().or(old.as_ref()) {
            message = match &old {
                Some(old) => group(old, "message").to_string(),
                None => lines.get(i + 1).map_or("", |l| l.trim()).to_string(),
            };
            let frame = Frame::new(group(caps, "file"), num(group(caps, "line")), "")
                .with_col(num(group(caps, "col")));
            panic_frame = Some(frame);
            continue;
        }
        if let Some(named) = RUST_FUNC.captures(line) {
            func = group(&named, "func").trim().to_string();
        } else if let Some(at) = RUST_AT.captures(line).filter(|_| !func.is_empty()) {
            let frame = Frame::new(group(&at, "file").trim(), num(group(&at, "line")), &func)
                .with_col(num(group(&at, "col")));
            frames.push(frame);
            func.clear();
        }
    }
    let panic_frame = panic_frame?;
    if !frames.iter().any(|f| same_place(f, &panic_frame)) {
        let index = first_app_index(&frames);
        frames.insert(index, panic_frame);
    }
    Some(Trace::new(
        "rust",
        vec![Block::new("panic", &message, frames)],
        text,
    ))
}

fn same_place(a: &Frame, b: &Frame) -> bool {
    a.line == b.line
        && (a.file.ends_with(lstrip_chars(&b.file, "./"))
            || b.file.ends_with(lstrip_chars(&a.file, "./")))
}

/// Where the panic location goes: before the first frame outside the std library (`/rustc/`).
fn first_app_index(frames: &[Frame]) -> usize {
    frames
        .iter()
        .position(|f| !f.file.contains("/rustc/") && !f.func.contains("core::"))
        .unwrap_or(frames.len())
}

pub fn detect_ruby(text: &str) -> bool {
    RUBY_SIGNATURE.is_match(text)
}

pub fn parse_ruby(text: &str) -> Option<Trace> {
    let mut frames = Vec::new();
    let (mut exc_type, mut message) = (String::new(), String::new());
    for line in lines(text) {
        let trimmed = line.trim();
        let stripped = trimmed.strip_prefix("from ").unwrap_or(trimmed).trim();
        let Some(frame) = RUBY_FRAME.captures(stripped) else {
            continue;
        };
        frames.push(Frame::new(
            group(&frame, "file"),
            num(group(&frame, "line")),
            group(&frame, "func"),
        ));
        if frames.len() == 1 {
            let end = frame.get(0).map_or(0, |m| m.end());
            let rest = lstrip_chars(&stripped[end..], ": ").trim();
            match RUBY_TYPE.captures(rest) {
                Some(typed) => {
                    exc_type = group(&typed, "type").to_string();
                    let start = typed.get(0).map_or(rest.len(), |m| m.start());
                    message = rest[..start].trim().to_string();
                }
                None => {
                    exc_type = "RuntimeError".to_string();
                    message = rest.to_string();
                }
            }
        }
    }
    if frames.is_empty() {
        return None;
    }
    Some(Trace::new(
        "ruby",
        vec![Block::new(&exc_type, &message, frames)],
        text,
    ))
}

pub fn detect_node(text: &str) -> bool {
    NODE_SIGNATURE.is_match(text)
}

pub fn parse_node(text: &str) -> Option<Trace> {
    let mut blocks: Vec<Block> = Vec::new();
    for line in lines(text) {
        let cause = NODE_CAUSE.captures(line);
        if let (Some(frame), Some(last)) = (NODE_FRAME.captures(line), blocks.last_mut()) {
            let parsed = Frame::new(
                group(&frame, "file"),
                num(group(&frame, "line")),
                group(&frame, "func").trim(),
            )
            .with_col(num(group(&frame, "col")));
            last.frames.push(parsed);
            continue;
        }
        let head_text = cause.as_ref().map_or(line, |c| group(c, "rest")).trim();
        let Some(head) = NODE_HEAD.captures(head_text) else {
            continue;
        };
        if cause.is_some() || blocks.is_empty() {
            let message = group(&head, "message").trim();
            let code = group(&head, "code");
            let message = if code.is_empty() {
                message.to_string()
            } else {
                format!("[{code}] {message}").trim().to_string()
            };
            blocks.push(Block::new(group(&head, "type"), &message, Vec::new()));
        }
    }
    if blocks.is_empty() {
        return None;
    }
    let root = blocks.len() - 1;
    let mut trace = Trace::new("javascript", blocks, text);
    trace.root = root;
    Some(trace)
}
