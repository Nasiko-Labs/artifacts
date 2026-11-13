//! Pure: a Python traceback into blocks. Python prints frames oldest first; they are reversed here so
//! the innermost frame comes first. A chained traceback (`During handling…` / `The above exception was
//! the direct cause…`) is split into blocks; the first block is the root cause, the last the one the
//! program died with.

use std::sync::LazyLock;

use regex::Regex;

use crate::trace_model::{Block, Frame, Trace};
use crate::util::{group, lines, num, re, starts_with_space};

static SIGNATURE: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?m)^\s*Traceback \(most recent call last\):"));
static CHAIN: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?m)^\s*(?:During handling of the above exception, another exception occurred:|",
        r"The above exception was the direct cause of the following exception:)\s*$"
    ))
});
static FRAME: LazyLock<Regex> = LazyLock::new(|| {
    re(r#"^\s*File "(?P<file>.+)", line (?P<line>[0-9]+)(?:, in (?P<func>.+))?\s*$"#)
});
static REPEATED: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\s*\[Previous line repeated ([0-9]+) more times?\]\s*$"));
static EXCEPTION: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<message>.*))?$"));

pub fn detect(text: &str) -> bool {
    SIGNATURE.is_match(text)
}

pub fn parse(text: &str) -> Option<Trace> {
    let mut blocks = Vec::new();
    let mut omitted = 0;
    for piece in CHAIN.split(text).filter(|p| SIGNATURE.is_match(p)) {
        let (block, repeats) = block(piece);
        blocks.push(block);
        omitted += repeats;
    }
    if blocks.is_empty() {
        return None;
    }
    let surface = blocks.len() - 1;
    let mut trace = Trace::new("python", blocks, text);
    trace.surface = surface;
    trace.omitted_repeats = omitted;
    Some(trace)
}

fn block(piece: &str) -> (Block, i64) {
    let start = SIGNATURE.find(piece).map_or(0, |m| m.end());
    let lines = lines(&piece[start..]);
    let mut frames: Vec<Frame> = Vec::new();
    let mut omitted = 0;
    let mut exception = String::new();
    let mut i = 0;
    while i < lines.len() {
        let line = lines[i];
        if let Some(caps) = FRAME.captures(line) {
            let (source, step) = source_line(&lines, i);
            let mut frame = Frame::new(
                group(&caps, "file"),
                num(group(&caps, "line")),
                group(&caps, "func").trim(),
            );
            frame.source = source;
            frames.push(frame);
            exception.clear(); // the exception line is the first unindented line after the last frame
            i += step;
            continue;
        }
        let repeated = REPEATED.captures(line);
        if let (Some(caps), Some(last)) = (repeated, frames.last_mut()) {
            let count = caps.get(1).map_or(0, |m| num(m.as_str()));
            omitted += count;
            last.repeat = last.repeat.saturating_add(count);
        } else if !line.trim().is_empty() && !starts_with_space(line) && exception.is_empty() {
            exception = line.trim().to_string();
        }
        i += 1;
    }
    let (exc_type, message) = split_exception(&exception);
    frames.reverse();
    (Block::new(&exc_type, &message, frames), omitted)
}

/// The indented source line under a frame, when the runtime printed one (and the caret line after it).
fn source_line(lines: &[&str], i: usize) -> (String, usize) {
    let Some(next) = lines.get(i + 1) else {
        return (String::new(), 1);
    };
    if starts_with_space(next)
        && !FRAME.is_match(next)
        && !REPEATED.is_match(next)
        && !next.trim().is_empty()
    {
        let caret = lines.get(i + 2).map(|l| l.trim()).unwrap_or("");
        let is_caret = !caret.is_empty() && caret.chars().all(|c| matches!(c, '^' | '~' | ' '));
        return (next.trim().to_string(), if is_caret { 3 } else { 2 });
    }
    (String::new(), 1)
}

pub fn split_exception(line: &str) -> (String, String) {
    if let Some(caps) = EXCEPTION.captures(line) {
        return (
            group(&caps, "type").to_string(),
            group(&caps, "message").trim().to_string(),
        );
    }
    ("Exception".to_string(), line.trim().to_string())
}
