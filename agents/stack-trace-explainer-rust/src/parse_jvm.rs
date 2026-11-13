//! Pure: managed-runtime traces, Java/Kotlin and C#. Both print the innermost frame first.
//!
//! Java: the first block is the surface exception; each `Caused by:` starts a block and the LAST one is
//! the root cause. `... 23 more` lines are not frames. C#: an inner exception (` ---> Type: message`) is
//! the root cause; its frames come first, then `--- End of inner exception stack trace ---`, then the
//! outer exception's frames.

use std::sync::LazyLock;

use regex::Regex;

use crate::trace_model::{Block, Frame, Trace};
use crate::util::{group, lines, num, re};

static JAVA_FRAME: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^\s*at (?P<func>[\w.$<>/\-]+)\((?P<file>[^():]+)(?::(?P<line>[0-9]+))?\)\s*$")
});
static JAVA_HEAD: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r#"^(?:Exception in thread "[^"]*"\s+)?(?P<type>[\w.$]+(?:Exception|Error|Throwable)\b"#,
        r"[\w.$]*)(?::\s?(?P<message>.*))?$"
    ))
});
static JAVA_SIGNATURE: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?m)^\s*at [\w.$<>/\-]+\([^()]*(?:\.java|\.kt|\.scala|\.groovy|Native Method|Unknown Source)",
        r#"[^()]*\)\s*$|^Exception in thread ""#
    ))
});
static CAUSED: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*Caused by:\s*(?P<rest>.*)$"));

static CS_FRAME: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^\s*at (?P<func>[\w.<>$+`\[\],| ]+?)\(.*\) in (?P<file>.+):line (?P<line>[0-9]+)\s*$")
});
static CS_SIGNATURE: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?m)^\s*at .+\) in .+:line [0-9]+\s*$|^Unhandled exception\."));
static CS_HEAD: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"^(?:Unhandled exception\.\s*)?(?P<type>[\w.`]+(?:Exception|Error)\b)(?::\s?(?P<message>.*))?$",
    )
});
const CS_INNER: &str = " ---> ";
static CS_END_INNER: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\s*--- End of inner exception stack trace ---\s*$"));

pub fn detect_java(text: &str) -> bool {
    JAVA_SIGNATURE.is_match(text)
}

pub fn detect_csharp(text: &str) -> bool {
    CS_SIGNATURE.is_match(text)
}

pub fn parse_java(text: &str) -> Option<Trace> {
    let mut blocks: Vec<Block> = Vec::new();
    for line in lines(text) {
        let caused = CAUSED.captures(line);
        let head_text = caused.as_ref().map_or(line, |c| group(c, "rest")).trim();
        let head = JAVA_HEAD.captures(head_text);
        let frame = JAVA_FRAME.captures(line);
        match (frame, blocks.last_mut()) {
            (Some(frame), Some(last)) => {
                let number = group(&frame, "line");
                if !number.is_empty() {
                    let func = strip_module(group(&frame, "func"));
                    last.frames
                        .push(Frame::new(group(&frame, "file"), num(number), func));
                }
            }
            _ => {
                if let Some(head) = head.filter(|_| caused.is_some() || blocks.is_empty()) {
                    blocks.push(Block::new(
                        group(&head, "type"),
                        group(&head, "message").trim(),
                        Vec::new(),
                    ));
                }
            }
        }
    }
    if blocks.is_empty() {
        return None;
    }
    let root = blocks.len() - 1;
    let mut trace = Trace::new("java", blocks, text);
    trace.root = root;
    Some(trace)
}

/// `java.base/java.lang.Thread.run` -> `java.lang.Thread.run` (Java 9+ module prefix).
fn strip_module(func: &str) -> &str {
    func.split_once('/').map_or(func, |(_, rest)| rest)
}

pub fn parse_csharp(text: &str) -> Option<Trace> {
    let lines = lines(text);
    let head_of = |line: &str| line.split(CS_INNER).next().unwrap_or("").trim().to_string();
    let start = lines.iter().position(|l| CS_HEAD.is_match(&head_of(l)))?;
    let mut heads = vec![lines[start].trim()];
    let mut i = start + 1;
    while i < lines.len() && lines[i].trim().starts_with("--->") {
        heads.push(lines[i].trim());
        i += 1;
    }
    let names: Vec<String> = heads
        .iter()
        .flat_map(|head| head.split(CS_INNER))
        .map(|h| {
            let h = h.trim();
            h.strip_prefix("--->").unwrap_or(h).trim().to_string()
        })
        .collect();
    let mut frames: Vec<Vec<Frame>> = vec![Vec::new(); names.len()];
    let mut current = names.len().saturating_sub(1); // the innermost exception's frames are printed first
    for line in &lines[i..] {
        if CS_END_INNER.is_match(line) {
            current = current.saturating_sub(1);
            continue;
        }
        if let (Some(caps), Some(bucket)) = (CS_FRAME.captures(line), frames.get_mut(current)) {
            bucket.push(Frame::new(
                group(&caps, "file").trim(),
                num(group(&caps, "line")),
                group(&caps, "func").trim(),
            ));
        }
    }
    let blocks: Vec<Block> = names
        .iter()
        .zip(frames)
        .map(|(name, own)| match CS_HEAD.captures(name) {
            Some(head) => Block::new(group(&head, "type"), group(&head, "message").trim(), own),
            None => Block::new("Exception", name, own),
        })
        .collect();
    let root = blocks.len().saturating_sub(1);
    let mut trace = Trace::new("csharp", blocks, text);
    trace.root = root;
    Some(trace)
}
