//! The turn: intake, then the one route it picked.
//!
//! ```text
//! intake -+- explain  (parse -> explain per trace -> check -> repair? -> render)
//!         +- attach   (pasted code for the earlier trace: explain again)
//!         +- followup (relatedness check if unclear, then one call over the earlier trace, code and answer)
//!         +- scope    (no error text: decline, or ask for the error in a reply)
//!         +- fixed reply
//! ```
//!
//! Stateless: everything is built fresh from this turn's input, so any replica can answer any message.
//! Earlier turns come only from the platform's chat history; the earlier trace is re-parsed from it in
//! code. No route pauses: every turn ends with a reply.

use serde_json::{Value, json};

use crate::checks::{self, CheckContext};
use crate::envelope::{self, Envelope};
use crate::explain::{self, Explained, Status};
use crate::frames;
use crate::inputs::{Incoming, fence};
use crate::intake::{self, Earlier, Route};
use crate::llm::{LlmError, TurnDeps};
use crate::paste::{CodeBlock, Paste};
use crate::prompts;
use crate::render;
use crate::restate;
use crate::smalltalk;
use crate::snippets;
use crate::trace_model::Trace;
use crate::trace_parse;
use crate::util::take_chars;

#[derive(Debug, Clone, PartialEq)]
pub struct TurnResult {
    pub reply: String,
    /// The envelope to attach as a data part.
    pub data: Option<Value>,
}

impl TurnResult {
    fn reply(text: &str) -> Self {
        Self {
            reply: text.to_string(),
            data: None,
        }
    }
}

pub async fn turn(incoming: &Incoming, deps: &TurnDeps) -> TurnResult {
    let route = intake::plan_turn(incoming);
    if let Route::Reply(text) = &route {
        return TurnResult::reply(text);
    }
    if deps.traceparent.is_none() {
        return TurnResult::reply(prompts::NO_TRACEPARENT_REPLY);
    }
    match route {
        Route::Reply(text) => TurnResult::reply(&text),
        Route::Explain {
            paste,
            trust_libraries,
        } => {
            explain_paste(
                incoming,
                deps,
                &paste,
                &paste.blocks,
                trust_libraries,
                false,
            )
            .await
        }
        Route::Json { earlier } => {
            explain_paste(
                incoming,
                deps,
                &earlier.paste,
                &earlier.blocks,
                earlier.trust_libraries,
                true,
            )
            .await
        }
        Route::Attach { blocks, earlier } => attach(incoming, deps, &earlier, &blocks).await,
        Route::Followup {
            earlier,
            clearly_related,
        } => {
            if !clearly_related
                && let Some(declined) = relatedness_gate(incoming, deps, &earlier).await
            {
                return declined;
            }
            followup(incoming, deps, &earlier).await
        }
        Route::Scope => scope_check(incoming, deps).await,
    }
}

async fn explain_paste(
    incoming: &Incoming,
    deps: &TurnDeps,
    pasted: &Paste,
    blocks: &[CodeBlock],
    trust: bool,
    rebuilt: bool,
) -> TurnResult {
    let traces: Vec<Trace> = pasted
        .traces
        .iter()
        .map(|t| trace_parse::parse(t, trust))
        .collect();
    let results = explain_all(
        incoming,
        deps,
        traces,
        blocks,
        &pasted.words,
        pasted.primary,
    )
    .await;
    if let Some(early) = results
        .get(pasted.primary)
        .and_then(|r| early_reply(r, &pasted.traces))
    {
        return early;
    }
    let env = envelope::build(&results, pasted.primary, pasted.skipped_traces);
    let mut notes = pasted.notes.clone();
    notes.extend(snippet_notes(&results));
    if rebuilt {
        notes.push(prompts::REBUILT_NOTE.to_string());
    }
    result(&env, incoming, notes)
}

/// Primary first. A trace gets a repair call only if every later trace still keeps one call.
async fn explain_all(
    incoming: &Incoming,
    deps: &TurnDeps,
    traces: Vec<Trace>,
    blocks: &[CodeBlock],
    words: &str,
    primary: usize,
) -> Vec<Explained> {
    let order: Vec<usize> = std::iter::once(primary)
        .chain((0..traces.len()).filter(|&i| i != primary))
        .collect();
    let mut results: Vec<Option<Explained>> = vec![None; traces.len()];
    for (k, &index) in order.iter().enumerate() {
        let Some(trace) = traces.get(index) else {
            continue;
        };
        let remaining = order.len() - k - 1;
        if deps.left() == 0 || !deps.has_time() {
            let cand = frames::candidate(trace);
            results[index] = Some(Explained::new(
                Status::Skipped,
                trace.clone(),
                cand,
                Vec::new(),
            ));
            continue;
        }
        let may_repair = deps.left() >= remaining + 2;
        results[index] =
            Some(explain::explain(deps, trace, blocks, words, &incoming.policy, may_repair).await);
    }
    results.into_iter().flatten().collect()
}

fn early_reply(primary: &Explained, traces: &[String]) -> Option<TurnResult> {
    let reply = match primary.status {
        Status::Ok => return None,
        Status::OffTopic => smalltalk::OFF_TOPIC_REPLY.clone(),
        Status::NotATrace => {
            return Some(TurnResult {
                reply: prompts::NOT_A_TRACE_REPLY.to_string(),
                data: Some(envelope::not_a_trace(&traces.join("\n"))),
            });
        }
        Status::Failed => prompts::MODEL_FAILED_REPLY.to_string(),
        Status::Unusable => prompts::MODEL_UNUSABLE_REPLY.to_string(),
        Status::Skipped => prompts::OUT_OF_BUDGET_REPLY.to_string(),
    };
    Some(TurnResult { reply, data: None })
}

fn snippet_notes(results: &[Explained]) -> Vec<String> {
    if results
        .iter()
        .any(|r| r.views.iter().any(|v| v.numbered_from_one))
    {
        return vec![prompts::NUMBERED_FROM_ONE_NOTE.to_string()];
    }
    Vec::new()
}

fn result(env: &Envelope, incoming: &Incoming, notes: Vec<String>) -> TurnResult {
    let mut all = incoming.notes.clone();
    all.extend(notes);
    let data = env.to_value();
    let reply = if incoming.wants_json {
        render::render_json(&data)
    } else {
        render::render(env, &all)
    };
    TurnResult {
        reply,
        data: Some(data),
    }
}

fn earlier_traces(earlier: &Earlier) -> Vec<Trace> {
    earlier
        .paste
        .traces
        .iter()
        .map(|t| trace_parse::parse(t, earlier.trust_libraries))
        .collect()
}

/// Pasted code for the earlier primary trace: re-explain it (one call).
async fn attach(
    incoming: &Incoming,
    deps: &TurnDeps,
    earlier: &Earlier,
    pasted: &[CodeBlock],
) -> TurnResult {
    let traces = earlier_traces(earlier);
    let primary = earlier.paste.primary;
    let Some(trace) = traces.get(primary) else {
        return TurnResult::reply(prompts::TRACE_TRIMMED);
    };
    let mut blocks = earlier.blocks.clone();
    blocks.extend(pasted.iter().cloned());
    let explained = explain::explain(
        deps,
        trace,
        &blocks,
        &earlier.paste.words,
        &incoming.policy,
        true,
    )
    .await;
    if let Some(early) = early_reply(&explained, std::slice::from_ref(&trace.raw)) {
        return early;
    }
    let mut env = envelope::build(
        std::slice::from_ref(&explained),
        0,
        earlier.paste.skipped_traces,
    );
    if let Some(first) = env.items.first_mut() {
        first.trace_index = primary + 1;
    }
    let mut notes = vec![prompts::REATTACHED_NOTE.to_string()];
    notes.extend(snippet_notes(std::slice::from_ref(&explained)));
    result(&env, incoming, notes)
}

async fn followup(incoming: &Incoming, deps: &TurnDeps, earlier: &Earlier) -> TurnResult {
    let traces = earlier_traces(earlier);
    let Some(trace) = traces.get(earlier.paste.primary) else {
        return TurnResult::reply(prompts::TRACE_TRIMMED);
    };
    let cand = frames::candidate(trace);
    let views = snippets::tie(trace, &earlier.blocks, cand.frame.as_ref());
    let previous = if earlier.last_reply.is_empty() {
        "(not in the recent conversation any more)"
    } else {
        earlier.last_reply.as_str()
    };
    let code: Vec<String> = views.iter().map(|v| fence(&v.render(), "CODE")).collect();
    let code = if code.is_empty() {
        "(none)".to_string()
    } else {
        code.join("\n\n")
    };
    let content = [
        format!("QUESTION:\n{}", fence(&incoming.text, "QUESTION")),
        format!("STORED RESULT:\n{}", fence(previous, "RESULT")),
        format!(
            "TRACE:\n{}",
            fence(take_chars(&trace.raw, explain::TRACE_PROMPT_CHARS), "TRACE")
        ),
        format!("CODE:\n{code}"),
    ]
    .join("\n\n");
    let messages = vec![
        prompts::system(prompts::FOLLOWUP_SYSTEM, &incoming.policy),
        prompts::user(&content),
    ];
    let answer = match call_with_repair(deps, &messages, &["ok", "off_topic"]).await {
        Ok(answer) => answer,
        Err(reply) => return reply,
    };
    if answer["status"] == "off_topic" {
        return TurnResult::reply(&smalltalk::OFF_TOPIC_REPLY);
    }
    let first = answer["answer"].as_str().unwrap_or("").to_string();
    let text = unrestated(deps, &messages, first, &earlier.last_reply).await;
    let ctx = CheckContext::new(trace, &views, cand.frame.as_ref());
    let (cleaned, removed) = checks::clean_prose(&text, &ctx);
    if cleaned.is_empty() {
        return TurnResult::reply(prompts::FOLLOWUP_UNANSWERED);
    }
    TurnResult::reply(&render::render_followup(&cleaned, removed))
}

/// An answer that mostly copies the previous reply gets one retry asking for the question itself; the
/// retry is kept when it is a usable answer that overlaps less, else the first answer stands.
async fn unrestated(deps: &TurnDeps, messages: &[Value], first: String, previous: &str) -> String {
    if !restate::restates(&first, previous) || !deps.can_call() {
        return first;
    }
    let mut retry = messages.to_vec();
    retry.push(prompts::assistant(
        &json!({"status": "ok", "answer": first}).to_string(),
    ));
    retry.push(prompts::user(prompts::RESTATED_RETRY));
    let second = match deps.call_json(&retry).await {
        Ok(Some(answer)) if answer["status"] == "ok" => {
            answer["answer"].as_str().unwrap_or("").to_string()
        }
        _ => return first,
    };
    if second.trim().is_empty()
        || restate::overlap(&second, previous) >= restate::overlap(&first, previous)
    {
        return first;
    }
    second
}

/// A message after an explanation that names nothing from it: one classify call decides whether it is
/// a follow-up (None: go on) or unrelated (the decline, or the failure reply).
async fn relatedness_gate(
    incoming: &Incoming,
    deps: &TurnDeps,
    earlier: &Earlier,
) -> Option<TurnResult> {
    let error = earlier
        .paste
        .traces
        .get(earlier.paste.primary)
        .map_or("", |t| take_chars(t, explain::TRACE_PROMPT_CHARS));
    let previous = if earlier.last_reply.is_empty() {
        "(not in the recent conversation any more)"
    } else {
        earlier.last_reply.as_str()
    };
    let content = format!(
        "ERROR:\n{}\n\nPREVIOUS ANSWER:\n{}\n\nQUESTION:\n{}",
        fence(error, "ERROR"),
        fence(previous, "RESULT"),
        fence(&incoming.text, "QUESTION")
    );
    let messages = vec![
        prompts::system(prompts::RELATED_SYSTEM, &incoming.policy),
        prompts::user(&content),
    ];
    match call_with_repair(deps, &messages, &["related", "off_topic"]).await {
        Ok(answer) if answer["status"] == "related" => None,
        Ok(_) => Some(TurnResult::reply(&smalltalk::OFF_TOPIC_REPLY)),
        Err(reply) => Some(reply),
    }
}

/// No error text and no earlier trace: one classify call decides between a decline and asking for
/// the error.
async fn scope_check(incoming: &Incoming, deps: &TurnDeps) -> TurnResult {
    let messages = vec![
        prompts::system(prompts::SCOPE_SYSTEM, &incoming.policy),
        prompts::user(&fence(&incoming.text, "MESSAGE")),
    ];
    match call_with_repair(deps, &messages, &["in_scope", "off_topic"]).await {
        Ok(answer) if answer["status"] == "in_scope" => TurnResult::reply(prompts::ASK_TRACE_TEXT),
        Ok(_) => TurnResult::reply(&smalltalk::OFF_TOPIC_REPLY),
        Err(reply) => reply,
    }
}

/// One call whose answer must be a JSON object with one of `statuses`; one repair call when it is not.
/// The error is the reply to give: "couldn't reach the model" only for a transport failure.
async fn call_with_repair(
    deps: &TurnDeps,
    messages: &[Value],
    statuses: &[&str],
) -> Result<Value, TurnResult> {
    let usable = |v: &Option<Value>| {
        v.as_ref()
            .and_then(|v| v["status"].as_str())
            .is_some_and(|s| statuses.contains(&s))
    };
    let first = deps
        .call_json(messages)
        .await
        .map_err(|e| llm_failure(&e))?;
    if usable(&first) {
        return Ok(first.unwrap_or_default());
    }
    if !deps.can_call() {
        return Err(TurnResult::reply(prompts::MODEL_UNUSABLE_REPLY));
    }
    let mut retry = messages.to_vec();
    retry.push(prompts::user(&format!(
        "Your previous answer was not the JSON object asked for. Return exactly one JSON object with \"status\" one of: {}.",
        statuses.join(", ")
    )));
    let second = deps.call_json(&retry).await.map_err(|e| llm_failure(&e))?;
    match second {
        Some(answer) if usable(&Some(answer.clone())) => Ok(answer),
        _ => Err(TurnResult::reply(prompts::MODEL_UNUSABLE_REPLY)),
    }
}

fn llm_failure(err: &LlmError) -> TurnResult {
    TurnResult::reply(match err {
        LlmError::Transport(_) => prompts::MODEL_FAILED_REPLY,
        LlmError::BadResponse(_) => prompts::MODEL_UNUSABLE_REPLY,
        LlmError::NoTraceparent => prompts::NO_TRACEPARENT_REPLY,
        LlmError::OutOfTime | LlmError::BudgetSpent => prompts::OUT_OF_BUDGET_REPLY,
    })
}
