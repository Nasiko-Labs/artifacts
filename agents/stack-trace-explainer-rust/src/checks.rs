//! Pure: verbatim and structure checks on the model's explanation. Nothing the model typed is trusted:
//!
//! 1. every evidence quote must be in the trace or a pasted code block (normalised); failing quotes are
//!    removed;
//! 2. fix_code must touch an identifier from the shown code, mention no unknown file, and hold no
//!    secret-looking string or network/shell call the user did not already use; else it is dropped;
//! 3. every file path, `path:line` and `line N` in the prose must be in the trace or code; else the
//!    sentence goes;
//! 4. need_code is kept only when it names the candidate file and a range containing its line.
//!
//! `problems` lists what failed, for the one repair call.

use std::sync::LazyLock;

use regex::Regex;
use unicode_normalization::UnicodeNormalization;

use crate::answer::{Answer, Evidence, NeedCode};
use crate::snippets::{CodeView, same_file};
use crate::trace_model::{Frame, Trace};
use crate::util::{collapse_ws, group, lines, num, re, take_chars};

static PATH: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"[\w./\\-]*\w\.(?:py|js|mjs|cjs|ts|tsx|jsx|java|kt|go|rs|rb|cs|php|scala|swift)(?::(?P<line>[0-9]+))?\b",
    )
});
static LINE_REF: LazyLock<Regex> = LazyLock::new(|| re(r"\b(?:line|L)\s?(?P<n>[0-9]{1,6})\b"));
static URL: LazyLock<Regex> = LazyLock::new(|| re(r"https?://[^\s)>\]]+"));
static HTML: LazyLock<Regex> = LazyLock::new(|| re(r"</?[A-Za-z][^>]*>"));
static VIEW_PREFIX: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*L[0-9]+:\s?"));
static IDENT: LazyLock<Regex> = LazyLock::new(|| re(r"[A-Za-z_]\w{2,}"));
static NEED_LINES: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*([0-9]+)\s*-\s*([0-9]+)\s*$"));
static RISKY: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r#"(?i)(?:api[_-]?key|secret|password|passwd|token)\s*[=:]\s*["'][^"']{6,}["']|AKIA[0-9A-Z]{16}|"#,
        r"\bsubprocess\b|\bos\.system\b|\bexec\(|\beval\(|child_process|Runtime\.getRuntime|",
        r"\brequests\.(?:get|post|put|delete)\b|\bfetch\(|\bcurl\b|\bwget\b|https?://"
    ))
});

pub fn normalise(text: &str) -> String {
    let nfkc: String = text.nfkc().collect();
    let straight: String = nfkc
        .chars()
        .map(|c| match c {
            '\u{2018}' | '\u{2019}' => '\'',
            '\u{201c}' | '\u{201d}' => '"',
            '\u{2013}' | '\u{2014}' => '-',
            other => other,
        })
        .collect();
    collapse_ws(&straight)
}

pub struct CheckContext<'a> {
    pub trace: &'a Trace,
    pub views: &'a [CodeView],
    pub candidate: Option<&'a Frame>,
    trace_text: String,
    code_text: String,
    paths: Vec<String>,
}

impl<'a> CheckContext<'a> {
    pub fn new(trace: &'a Trace, views: &'a [CodeView], candidate: Option<&'a Frame>) -> Self {
        let code: Vec<&str> = views
            .iter()
            .flat_map(|v| v.lines.iter().map(String::as_str))
            .collect();
        Self {
            trace,
            views,
            candidate,
            trace_text: normalise(&trace.raw),
            code_text: normalise(&code.join("\n")),
            paths: views.iter().map(|v| normalise(&v.path)).collect(),
        }
    }

    pub fn known(&self, token: &str) -> bool {
        let needle = normalise(token);
        !needle.is_empty()
            && (self.trace_text.contains(&needle)
                || self.code_text.contains(&needle)
                || self.paths.iter().any(|p| p.contains(&needle)))
    }

    pub fn line_known(&self, number: i64, path: &str) -> bool {
        let in_trace = self
            .trace
            .all_frames()
            .iter()
            .any(|f| (path.is_empty() || same_file(path, &f.file)) && f.line == number);
        in_trace
            || self
                .views
                .iter()
                .any(|v| v.has_line(number) && (path.is_empty() || same_file(path, &v.path)))
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Checked {
    pub result: Answer,
    pub need_code: Option<NeedCode>,
    pub problems: Vec<String>,
    pub quotes_removed: usize,
    pub quotes_kept: usize,
    pub code_quotes: usize,
    pub sentences_removed: usize,
    pub fix_code_dropped: String,
    pub need_code_ignored: bool,
}

impl Checked {
    fn new(result: Answer) -> Self {
        Self {
            result,
            need_code: None,
            problems: Vec::new(),
            quotes_removed: 0,
            quotes_kept: 0,
            code_quotes: 0,
            sentences_removed: 0,
            fix_code_dropped: String::new(),
            need_code_ignored: false,
        }
    }
}

pub fn run(result: &Answer, ctx: &CheckContext) -> Checked {
    let mut out = Checked::new(result.clone());
    evidence(&mut out, ctx);
    fix_code(&mut out, ctx);
    let mut answer = out.result.clone();
    answer.likely_cause = clean_text(
        &mut out,
        ctx,
        "likely_cause",
        answer.likely_cause.as_deref(),
    );
    answer.fix_suggestion = clean_text(
        &mut out,
        ctx,
        "fix_suggestion",
        answer.fix_suggestion.as_deref(),
    );
    answer.frame_reason = clean_text(
        &mut out,
        ctx,
        "frame_reason",
        answer.frame_reason.as_deref(),
    );
    answer.confidence_notes = clean_text(
        &mut out,
        ctx,
        "confidence_notes",
        answer.confidence_notes.as_deref(),
    );
    answer.how_to_verify = answer
        .how_to_verify
        .iter()
        .filter_map(|s| clean_text(&mut out, ctx, "how_to_verify", Some(s)))
        .collect();
    out.need_code = need_code(&mut out, ctx);
    out.result = answer;
    out
}

fn evidence(out: &mut Checked, ctx: &CheckContext) {
    let mut kept = Vec::new();
    for item in &out.result.evidence {
        let quote = VIEW_PREFIX.replace(&item.quote, "").trim().to_string();
        let needle = normalise(&quote);
        if !needle.is_empty() && ctx.code_text.contains(&needle) {
            kept.push(Evidence {
                source: "code".into(),
                quote,
            });
            out.code_quotes += 1;
        } else if !needle.is_empty() && ctx.trace_text.contains(&needle) {
            kept.push(Evidence {
                source: "trace".into(),
                quote,
            });
        } else {
            out.quotes_removed += 1;
            out.problems.push(format!(
                "evidence quote not found verbatim in the data: {:?}",
                take_chars(&quote, 120)
            ));
        }
    }
    out.quotes_kept = kept.len();
    out.result.evidence = kept;
}

fn fix_code(out: &mut Checked, ctx: &CheckContext) {
    let Some(code) = out.result.fix_code.clone() else {
        return;
    };
    let unknown = path_matches(&code)
        .into_iter()
        .map(|(whole, _)| whole)
        .find(|whole| !ctx.known(whole.split(':').next().unwrap_or("")));
    let risky = RISKY
        .find_iter(&code)
        .map(|m| m.as_str())
        .find(|m| !ctx.known(m));
    let reason = if let Some(path) = unknown {
        format!("fix_code mentions a file not in the data: {path}")
    } else if let Some(risk) = risky {
        format!("fix_code adds a secret-looking value or a network/shell call: {risk}")
    } else if !IDENT.find_iter(&code).any(|w| ctx.known(w.as_str())) {
        "fix_code touches no identifier from the trace or code".to_string()
    } else {
        return;
    };
    out.fix_code_dropped = reason.clone();
    out.problems.push(reason);
    out.result.fix_code = None;
}

/// File-path matches (`whole`, optional line) not preceded by a word char or `/`.
fn path_matches(text: &str) -> Vec<(String, Option<i64>)> {
    PATH.captures_iter(text)
        .filter(|caps| {
            let start = caps.get(0).map_or(0, |m| m.start());
            !text[..start]
                .chars()
                .next_back()
                .is_some_and(|c| c.is_alphanumeric() || c == '_' || c == '/')
        })
        .map(|caps| {
            let whole = caps.get(0).map_or("", |m| m.as_str()).to_string();
            let line = Some(group(&caps, "line"))
                .filter(|l| !l.is_empty())
                .map(num);
            (whole, line)
        })
        .collect()
}

/// `(?<=[.!?])\s+` split: sentence pieces, each without the whitespace that ended the previous one.
pub fn sentences(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        let ends = matches!(current.chars().last(), Some('.' | '!' | '?'));
        if c.is_whitespace() && ends {
            while chars.peek().is_some_and(|n| n.is_whitespace()) {
                chars.next();
            }
            out.push(std::mem::take(&mut current));
            continue;
        }
        current.push(c);
    }
    out.push(current);
    out
}

fn clean_text(
    out: &mut Checked,
    ctx: &CheckContext,
    name: &str,
    value: Option<&str>,
) -> Option<String> {
    let value = value.map(str::trim).filter(|v| !v.is_empty())?;
    let mut kept = Vec::new();
    for sentence in sentences(value) {
        let bad = bad_reference(&sentence, ctx);
        if !bad.is_empty() {
            out.problems.push(format!(
                "{name} cites {bad:?}, which is not in the trace or code"
            ));
            out.sentences_removed += 1;
            continue;
        }
        kept.push(HTML.replace_all(&sentence, "").into_owned());
    }
    let joined = kept.join(" ");
    if joined.is_empty() {
        None
    } else {
        Some(joined)
    }
}

fn bad_reference(sentence: &str, ctx: &CheckContext) -> String {
    for url in URL.find_iter(sentence) {
        if !ctx.known(url.as_str().trim_end_matches(['.', ','])) {
            return url.as_str().to_string();
        }
    }
    for (whole, line) in path_matches(sentence) {
        let path = whole.split(':').next().unwrap_or("");
        let in_trace = ctx
            .trace
            .all_frames()
            .iter()
            .any(|f| same_file(path, &f.file));
        if !ctx.known(path) && !in_trace {
            return whole;
        }
        if line.is_some_and(|n| !ctx.line_known(n, path)) {
            return whole;
        }
    }
    for caps in LINE_REF.captures_iter(sentence) {
        if !ctx.line_known(num(group(&caps, "n")), "") {
            return caps.get(0).map_or("", |m| m.as_str()).to_string();
        }
    }
    String::new()
}

fn need_code(out: &mut Checked, ctx: &CheckContext) -> Option<NeedCode> {
    let need = out.result.need_code.as_ref().filter(|m| !m.is_empty())?;
    let file = need
        .get("file")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let range = need
        .get("lines")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let parsed = NEED_LINES.captures(&range).map(|c| {
        (
            num(c.get(1).map_or("", |m| m.as_str())),
            num(c.get(2).map_or("", |m| m.as_str())),
        )
    });
    match (ctx.candidate, parsed) {
        (Some(frame), Some((a, b)))
            if same_file(&file, &frame.file) && a <= frame.line && frame.line <= b =>
        {
            Some(NeedCode {
                file: frame.file.clone(),
                lines: format!("{a}-{b}"),
            })
        }
        _ => {
            out.need_code_ignored = true;
            None
        }
    }
}

/// Free text (a follow-up answer), line by line, with every prose sentence citing an unknown path, line
/// or URL removed. Lines inside a ``` fence are kept as written (a test or a change the user asked for).
pub fn clean_prose(text: &str, ctx: &CheckContext) -> (String, usize) {
    let mut out = Checked::new(empty_answer());
    let mut kept: Vec<String> = Vec::new();
    let mut in_fence = false;
    for line in lines(text) {
        if line.trim().starts_with("```") {
            in_fence = !in_fence;
            kept.push(line.to_string());
            continue;
        }
        if in_fence || line.trim().is_empty() {
            kept.push(line.to_string());
            continue;
        }
        if let Some(cleaned) = clean_text(&mut out, ctx, "answer", Some(line)) {
            kept.push(cleaned);
        }
    }
    (kept.join("\n").trim().to_string(), out.sentences_removed)
}

fn empty_answer() -> Answer {
    Answer {
        frame_index: serde_json::Value::Null,
        frame_reason: None,
        likely_cause: None,
        fix_suggestion: None,
        fix_code: None,
        how_to_verify: Vec::new(),
        evidence: Vec::new(),
        need_code: None,
        confidence_notes: None,
    }
}
