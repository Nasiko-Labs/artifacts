//! One trace explained: the fenced data the model sees, the call, the code checks and at most one
//! repair call. The location shown is resolved by code from a parsed frame; the model may only move it
//! to another app frame by index.

use serde_json::Value;

use crate::answer::{self, Answer, Validated};
use crate::checks::{self, CheckContext, Checked};
use crate::frames::{self, Candidate};
use crate::hints;
use crate::inputs::fence;
use crate::llm::{LlmError, TurnDeps};
use crate::paste::CodeBlock;
use crate::prompts;
use crate::snippets::{self, CodeView};
use crate::trace_model::Trace;
use crate::util::{char_len, last_chars, take_chars, thousands};

pub const TRACE_PROMPT_CHARS: usize = 6_000;
const TRACE_HEAD_CHARS: usize = 4_000;
const TRACE_TAIL_CHARS: usize = 2_000;
const FRAMES_HEAD: usize = 40;
const FRAMES_TAIL: usize = 20;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok,
    OffTopic,
    NotATrace,
    /// The model could not be reached (transport or HTTP failure).
    Failed,
    /// The model answered, but not in the schema, even after the repair call.
    Unusable,
    /// No call or time left for this trace.
    Skipped,
}

#[derive(Debug, Clone)]
pub struct Explained {
    pub status: Status,
    pub trace: Trace,
    pub candidate: Candidate,
    pub views: Vec<CodeView>,
    pub checked: Option<Checked>,
    pub repairs: usize,
    /// The model picked another app frame and code accepted it.
    pub moved: bool,
    /// Check failures sent to the one repair call.
    pub repaired_problems: usize,
}

impl Explained {
    pub fn new(status: Status, trace: Trace, candidate: Candidate, views: Vec<CodeView>) -> Self {
        Self {
            status,
            trace,
            candidate,
            views,
            checked: None,
            repairs: 0,
            moved: false,
            repaired_problems: 0,
        }
    }

    pub fn view(&self) -> Option<&CodeView> {
        snippets::candidate_view(&self.views, self.candidate.frame.as_ref())
    }
}

pub fn frame_table(trace: &Trace) -> String {
    let mut rows = Vec::new();
    for (index, block) in trace.blocks.iter().enumerate() {
        let role = if index == trace.root {
            "ROOT CAUSE"
        } else if index == trace.surface {
            "SURFACE"
        } else {
            "CHAIN"
        };
        rows.push(format!("[{role}] {}", block.headline()));
        let frames = &block.frames;
        let skip = frames.len() > FRAMES_HEAD + FRAMES_TAIL;
        for (i, frame) in frames.iter().enumerate() {
            if skip && i == FRAMES_HEAD {
                rows.push(format!(
                    "  ... {} frames omitted",
                    frames.len() - FRAMES_HEAD - FRAMES_TAIL
                ));
            }
            if skip && i >= FRAMES_HEAD && i < frames.len() - FRAMES_TAIL {
                continue;
            }
            let repeat = if frame.repeat > 1 {
                format!(" (repeated {} times)", frame.repeat)
            } else {
                String::new()
            };
            let source = if frame.source.is_empty() {
                String::new()
            } else {
                format!(" | {}", frame.source)
            };
            rows.push(format!(
                "  {}: {}{repeat}{source}",
                frame.origin.as_str(),
                frame.label()
            ));
        }
    }
    rows.join("\n")
}

fn trace_excerpt(trace: &Trace) -> String {
    let raw = &trace.raw;
    let size = char_len(raw);
    if size <= TRACE_PROMPT_CHARS {
        return raw.clone();
    }
    format!(
        "{}\n... [{} characters omitted] ...\n{}",
        take_chars(raw, TRACE_HEAD_CHARS),
        thousands(size - TRACE_PROMPT_CHARS),
        last_chars(raw, TRACE_TAIL_CHARS)
    )
}

pub fn user_message(trace: &Trace, cand: &Candidate, views: &[CodeView], words: &str) -> String {
    let app: Vec<String> = frames::app_frames(trace)
        .iter()
        .enumerate()
        .map(|(i, f)| format!("  {i}: {}", f.label()))
        .collect();
    let app_rows = if app.is_empty() {
        "  (none)".to_string()
    } else {
        app.join("\n")
    };
    let place = cand
        .frame
        .as_ref()
        .map_or("none (no frame was parsed)".to_string(), |f| f.label());
    let led = cand.led_from.as_ref().map_or(String::new(), |f| {
        format!("; the call that led there: {}", f.label())
    });
    let root = trace.root_block();
    let hint = match hints::hint_for(&root.exception_type, &root.message) {
        "" => "(no hint for this error)",
        text => text,
    };
    let code: Vec<String> = views.iter().map(view_text).collect();
    let code = if code.is_empty() {
        "(no code was pasted)".to_string()
    } else {
        code.join("\n\n")
    };
    let frameless = if trace.frameless {
        " (frameless: no stack frames were found)"
    } else {
        ""
    };
    let words = if words.is_empty() { "(none)" } else { words };
    [
        format!("USER WORDS:\n{}", fence(words, "REQUEST")),
        format!("LANGUAGE: {}{frameless}", trace.language),
        format!(
            "CANDIDATE (computed by code): {place} [{}]{led}",
            cand.origin.as_str()
        ),
        format!("APP FRAMES (frame_index choices, innermost first):\n{app_rows}"),
        format!("FRAMES:\n{}", fence(&frame_table(trace), "FRAMES")),
        format!("HINT (background, not evidence): {hint}"),
        format!("TRACE:\n{}", fence(&trace_excerpt(trace), "TRACE")),
        format!("CODE:\n{code}"),
    ]
    .join("\n\n")
}

fn view_text(view: &CodeView) -> String {
    let note = if view.numbered_from_one {
        " (line numbers start at 1 in the snippet; they may not match the file)"
    } else {
        ""
    };
    let path = if view.path.is_empty() {
        "unlabelled snippet"
    } else {
        view.path.as_str()
    };
    format!("file: {path}{note}\n{}", fence(&view.render(), "CODE"))
}

fn failure_status(err: &LlmError) -> Status {
    match err {
        LlmError::Transport(_) => Status::Failed,
        LlmError::BadResponse(_) => Status::Unusable,
        LlmError::NoTraceparent | LlmError::OutOfTime | LlmError::BudgetSpent => Status::Skipped,
    }
}

pub async fn explain(
    deps: &TurnDeps,
    trace: &Trace,
    blocks: &[CodeBlock],
    words: &str,
    policy: &[String],
    may_repair: bool,
) -> Explained {
    let cand = frames::candidate(trace);
    let views = snippets::tie(trace, blocks, cand.frame.as_ref());
    let base_user = user_message(trace, &cand, &views, words);
    let messages = vec![
        prompts::system(prompts::EXPLAIN_SYSTEM, policy),
        prompts::user(&base_user),
    ];
    let first = match deps.call_json(&messages).await {
        Ok(first) => first,
        Err(err) => return Explained::new(failure_status(&err), trace.clone(), cand, views),
    };
    let mut data = answer::validate(first.as_ref());
    let mut repairs = 0;
    if data.is_none() && may_repair && deps.can_call() {
        repairs = 1;
        let problems = vec!["the answer was not the JSON object asked for".to_string()];
        let previous = first.unwrap_or_else(|| Value::Object(Default::default()));
        let repaired = safe_call(
            deps,
            &prompts::repair_messages(&base_user, &previous, &problems, policy),
        )
        .await;
        data = answer::validate(repaired.as_ref());
    }
    let mut result = Explained::new(Status::Unusable, trace.clone(), cand, views);
    result.repairs = repairs;
    match data {
        None => result,
        Some(Validated::Sentinel(status)) => {
            result.status = if status == "off_topic" {
                Status::OffTopic
            } else {
                Status::NotATrace
            };
            result
        }
        Some(Validated::Ok(answer)) => {
            check(
                deps,
                result,
                *answer,
                &base_user,
                policy,
                may_repair && repairs == 0,
            )
            .await
        }
    }
}

async fn check(
    deps: &TurnDeps,
    mut result: Explained,
    data: Answer,
    base_user: &str,
    policy: &[String],
    may_repair: bool,
) -> Explained {
    let (chosen, moved) = frames::pick(&result.trace, &result.candidate, &data.frame_index);
    let mut data = data;
    if !moved {
        data.frame_reason = None;
    }
    if moved {
        let blocks = blocks_of(&result.views);
        result.views = snippets::tie(&result.trace, &blocks, chosen.frame.as_ref());
    }
    result.status = Status::Ok;
    result.moved = moved;
    let checked = {
        let ctx = CheckContext::new(&result.trace, &result.views, chosen.frame.as_ref());
        checks::run(&data, &ctx)
    };
    result.candidate = chosen;
    if checked.problems.is_empty() || !may_repair || !deps.can_call() {
        result.checked = Some(checked);
        return result;
    }
    tracing::info!(
        "explanation failed {} checks; one repair call",
        checked.problems.len()
    );
    let messages = prompts::repair_messages(base_user, &data.to_json(), &checked.problems, policy);
    let repaired = answer::validate(safe_call(deps, &messages).await.as_ref());
    result.repairs = 1;
    result.repaired_problems = checked.problems.len();
    result.checked = Some(match repaired {
        Some(Validated::Ok(mut again)) => {
            again.frame_index = data.frame_index.clone();
            if !moved {
                again.frame_reason = None;
            }
            let ctx = CheckContext::new(
                &result.trace,
                &result.views,
                result.candidate.frame.as_ref(),
            );
            checks::run(&again, &ctx)
        }
        _ => checked,
    });
    result
}

fn blocks_of(views: &[CodeView]) -> Vec<CodeBlock> {
    views
        .iter()
        .map(|v| CodeBlock {
            path: v.path.clone(),
            lines: v.lines.clone(),
            start: v.start,
            explicit_start: !v.numbered_from_one,
            truncated: false,
        })
        .collect()
}

async fn safe_call(deps: &TurnDeps, messages: &[Value]) -> Option<Value> {
    deps.call_json(messages).await.ok().flatten()
}
