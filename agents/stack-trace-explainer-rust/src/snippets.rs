//! Pure: tie pasted code blocks to trace frames and build the numbered views (`L41: ...`) the model cites.
//!
//! A block is tied to a frame by its label (same file), else, when unlabelled, by the frame's function
//! name found in it (`def total(`, `function total(`). Numbering: an explicit `lines 40-70` wins; else,
//! when the runtime printed the frame's source line and that line is in the block, the block is aligned
//! so it sits at the frame's line number; else numbering starts at 1 and the reply says so.

use std::sync::LazyLock;

use regex::Regex;

use crate::paste::CodeBlock;
use crate::trace_model::{Frame, Origin, Trace};
use crate::util::{collapse_ws, lstrip_chars, re, strip_chars};

pub const WINDOW: i64 = 12;
pub const MAX_VIEW_LINES: i64 = 60;
pub const MAX_VIEWS: usize = 4;

static AS_SUFFIX: LazyLock<Regex> = LazyLock::new(|| re(r"\s*\[as [^\]]+\]$"));
static NAME_SPLIT: LazyLock<Regex> = LazyLock::new(|| re(r"[.:#/]"));
static WORD: LazyLock<Regex> = LazyLock::new(|| re(r"^\w+$"));

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CodeView {
    pub path: String,
    /// Number of `lines[0]`.
    pub start: i64,
    /// The whole (aligned) block.
    pub lines: Vec<String>,
    /// The frame line this view is about, when it is inside the block.
    pub focus: Option<i64>,
    pub numbered_from_one: bool,
}

impl CodeView {
    fn len(&self) -> i64 {
        i64::try_from(self.lines.len()).unwrap_or(i64::MAX)
    }

    pub fn has_line(&self, number: i64) -> bool {
        self.start <= number && number < self.start.saturating_add(self.len())
    }

    pub fn line(&self, number: i64) -> &str {
        if !self.has_line(number) {
            return "";
        }
        usize::try_from(number - self.start)
            .ok()
            .and_then(|i| self.lines.get(i))
            .map_or("", String::as_str)
    }

    pub fn render(&self) -> String {
        let last_line = self.start.saturating_add(self.len()) - 1;
        let (first, last) = match self.focus {
            Some(focus) => (
                self.start.max(focus - WINDOW),
                last_line.min(focus + WINDOW),
            ),
            None => (self.start, last_line.min(self.start + MAX_VIEW_LINES - 1)),
        };
        (first..=last)
            .map(|n| format!("L{n}: {}", self.line(n)))
            .collect::<Vec<_>>()
            .join("\n")
    }
}

/// `com.acme.Repo.findById` / `main.(*Server).handle` / `Layer.handle [as x]` -> the bare function name.
pub fn short_name(func: &str) -> String {
    let name = AS_SUFFIX.replace(func, "");
    let name = name.trim();
    let last = NAME_SPLIT.split(name).last().unwrap_or("");
    strip_chars(last, "()*<>$ ").to_string()
}

fn normal_path(path: &str) -> String {
    lstrip_chars(&path.replace('\\', "/"), "./")
        .trim()
        .to_string()
}

pub fn same_file(block_path: &str, frame_file: &str) -> bool {
    let a = normal_path(block_path);
    let b = normal_path(frame_file);
    if a.is_empty() || b.is_empty() {
        return false;
    }
    let base = |p: &str| p.rsplit('/').next().unwrap_or("").to_string();
    a == b || a.ends_with(&format!("/{b}")) || b.ends_with(&format!("/{a}")) || base(&a) == base(&b)
}

/// Index of the line in `block` that defines `func`, if any.
pub fn defines(block: &CodeBlock, func: &str) -> Option<usize> {
    let name = short_name(func);
    if name.is_empty() || !WORD.is_match(&name) {
        return None;
    }
    let n = regex::escape(&name);
    let pattern = Regex::new(&format!(
        concat!(
            r"\b(?:def|async def|function|func|fn|sub)\s+(?:\([^)]*\)\s*)?{n}\s*[(<]|",
            r"\b{n}\s*\([^)]*\)\s*(?:\{{|=>|throws\b|:\s*$)|\b{n}\s*=\s*",
            r"(?:async\s*)?(?:function\b|\()"
        ),
        n = n
    ))
    .ok()?;
    block.lines.iter().position(|line| pattern.is_match(line))
}

fn source_index(block: &CodeBlock, frame: &Frame) -> Option<usize> {
    let wanted = collapse_ws(&frame.source);
    if wanted.is_empty() {
        return None;
    }
    block
        .lines
        .iter()
        .position(|line| collapse_ws(line) == wanted)
}

pub fn view_for(block: &CodeBlock, frame: Option<&Frame>) -> CodeView {
    let (mut start, mut from_one) = (block.start, !block.explicit_start);
    let source_at = frame
        .filter(|_| !block.explicit_start)
        .and_then(|f| source_index(block, f).map(|i| (f, i)));
    if let Some((frame, index)) = source_at {
        let aligned = frame.line - i64::try_from(index).unwrap_or(i64::MAX);
        if aligned >= 1 {
            start = aligned;
            from_one = false;
        }
    }
    let path = if block.path.is_empty() {
        frame.map_or(String::new(), |f| f.file.clone())
    } else {
        block.path.clone()
    };
    let mut view = CodeView {
        path,
        start,
        lines: block.lines.clone(),
        focus: None,
        numbered_from_one: from_one,
    };
    if let Some(frame) = frame.filter(|f| view.has_line(f.line) && !from_one) {
        view.focus = Some(frame.line);
    }
    view
}

/// One view per pasted block, the candidate's first. A block is tied to the candidate frame when it
/// names the same file or (unlabelled) defines the candidate's function; else to any app frame of the
/// trace it names.
pub fn tie(trace: &Trace, blocks: &[CodeBlock], candidate: Option<&Frame>) -> Vec<CodeView> {
    let app: Vec<&Frame> = trace
        .all_frames()
        .into_iter()
        .filter(|f| f.origin == Origin::App)
        .collect();
    let mut views: Vec<(u8, CodeView)> = blocks
        .iter()
        .map(|block| {
            let frame = frame_for(block, candidate, &app);
            let rank = if frame.is_some() && frame == candidate {
                0
            } else {
                1
            };
            (rank, view_for(block, frame))
        })
        .collect();
    views.sort_by_key(|(rank, _)| *rank);
    views.into_iter().map(|(_, v)| v).take(MAX_VIEWS).collect()
}

fn frame_for<'a>(
    block: &CodeBlock,
    candidate: Option<&'a Frame>,
    app: &[&'a Frame],
) -> Option<&'a Frame> {
    if !block.path.is_empty() {
        if let Some(c) = candidate.filter(|c| same_file(&block.path, &c.file)) {
            return Some(c);
        }
        return app
            .iter()
            .find(|f| same_file(&block.path, &f.file))
            .copied();
    }
    if let Some(c) =
        candidate.filter(|c| defines(block, &c.func).is_some() || source_index(block, c).is_some())
    {
        return Some(c);
    }
    app.iter()
        .find(|f| defines(block, &f.func).is_some())
        .copied()
}

/// The view that holds the candidate frame's line, when pasted code covers it.
pub fn candidate_view<'a>(views: &'a [CodeView], frame: Option<&Frame>) -> Option<&'a CodeView> {
    let frame = frame?;
    views.iter().find(|v| {
        v.focus == Some(frame.line) && (v.path.is_empty() || same_file(&v.path, &frame.file))
    })
}
