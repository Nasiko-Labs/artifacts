//! The model steps: write or revise a reply (one call, at most one repair, then code fixes).

use serde_json::{Value, json};

use crate::checks::{self, Basis, Fixed};
use crate::draft::{Answer, Reply, parse_answer};
use crate::grounding::Ground;
use crate::incoming::Incoming;
use crate::llm::{LlmError, TurnDeps};
use crate::prompts::{self, StyleBlock};
use crate::register::register_for;
use crate::render::sanitize;
use crate::style::{self, Metrics};
use crate::tone_check::{self, Target};

#[derive(Debug, Clone, Default)]
pub struct Job {
    pub incoming: Option<Incoming>,
    /// The purpose (new reply) or the revision instruction.
    pub instruction: String,
    /// The user's own words for this reply: they ground facts and links.
    pub words: Vec<String>,
    pub org: Vec<String>,
    pub channel: String,
    pub metrics: Metrics,
    /// Set for a revision.
    pub current: Option<Reply>,
    pub relaxed: bool,
    pub short: bool,
    pub want_subject: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Composed {
    Ok {
        reply: Reply,
        notes: Vec<String>,
        /// Problems remained after the repair and code fixed them.
        code_fixed: bool,
        ungrounded_after_repair: bool,
    },
    NeedsInfo,
    OffTopic,
    Failed,
}

pub fn target(job: &Job) -> Target {
    Target {
        metrics: job.metrics.clone(),
        register: register_for(&job.channel),
        sender: job
            .incoming
            .as_ref()
            .map_or(String::new(), |i| i.sender.clone()),
        relaxed: job.relaxed,
        short: job.short,
    }
}

pub fn basis(job: &Job) -> Basis {
    let incoming = job
        .incoming
        .as_ref()
        .map_or(String::new(), |i| i.text.clone());
    let tgt = target(job);
    let mut ground: Vec<&str> = vec![incoming.as_str()];
    ground.extend(job.words.iter().map(String::as_str));
    let greeting = tone_check::expected_greeting(&tgt);
    let allowed = [
        greeting.as_str(),
        job.metrics.sign_off.as_str(),
        tgt.sender.as_str(),
    ];
    Basis {
        ground: Ground::of(&ground),
        strict: Ground::of(&job.words),
        allowed: Ground::of(&allowed),
        incoming,
        target: tgt,
    }
}

fn messages(deps: &TurnDeps, job: &Job) -> Vec<Value> {
    let tgt = target(job);
    let reg = register_for(&job.channel);
    let greeting = if reg.greeting && job.metrics.greeting != style::NONE {
        tone_check::expected_greeting(&tgt)
    } else {
        String::new()
    };
    let sign_off = if reg.sign_off && job.metrics.sign_off != style::NONE {
        job.metrics.sign_off.replace('\n', " / ")
    } else {
        String::new()
    };
    let register = format!(
        "{}: {} Cap: {} words.",
        reg.label, reg.guidance, reg.max_words
    );
    let descriptors = style::descriptors(&job.metrics, job.short);
    let block = StyleBlock {
        style: &descriptors,
        register: &register,
        sender: &tgt.sender,
        greeting: &greeting,
        sign_off: &sign_off,
    };
    let user = match (&job.current, &job.incoming) {
        (Some(current), Some(incoming)) => {
            prompts::revise_user(&incoming.text, &job.instruction, &current.draft, &block)
        }
        _ => prompts::draft_user(
            job.incoming.as_ref().map(|i| i.text.as_str()),
            &job.instruction,
            &block,
        ),
    };
    vec![
        json!({"role": "system", "content": prompts::system(deps.today, &job.org)}),
        json!({"role": "user", "content": user}),
    ]
}

pub async fn compose(deps: &TurnDeps, job: &Job) -> Result<Composed, LlmError> {
    let messages = messages(deps, job);
    let mut data = deps.ask_json(&messages).await?;
    let mut answer = parse_answer(data.as_ref());
    if answer == Answer::Invalid && deps.can_call() {
        let violations = vec!["the answer was not the JSON object described".to_string()];
        data = deps
            .ask_json(&repair(&messages, data.as_ref(), &violations))
            .await?;
        answer = parse_answer(data.as_ref());
    }
    match answer {
        Answer::OffTopic => Ok(Composed::OffTopic),
        Answer::NeedsInfo(_) => Ok(Composed::NeedsInfo),
        _ if job.incoming.is_none() => Ok(Composed::NeedsInfo),
        Answer::Ok(reply) => checked(deps, job, &messages, data.as_ref(), reply).await,
        Answer::Invalid => Ok(Composed::Failed),
    }
}

async fn checked(
    deps: &TurnDeps,
    job: &Job,
    messages: &[Value],
    data: Option<&Value>,
    first: Reply,
) -> Result<Composed, LlmError> {
    let b = basis(job);
    let mut reply = clean(&first, job);
    let mut found = checks::problems(&reply, &b);
    if !found.is_empty() && deps.can_call() {
        tracing::info!("repairing reply: {} problem(s)", found.len());
        let repaired = deps.ask_json(&repair(messages, data, &found)).await?;
        if let Answer::Ok(again) = parse_answer(repaired.as_ref()) {
            reply = clean(&again, job);
            found = checks::problems(&reply, &b);
        }
    }
    let left = !checks::spans(&reply, &b).is_empty();
    let fixed = if found.is_empty() {
        Fixed {
            reply,
            notes: Vec::new(),
        }
    } else {
        checks::fix(&reply, &b)
    };
    let asks = checks::kept_asks(&fixed.reply.asks, &b.incoming);
    Ok(Composed::Ok {
        reply: Reply {
            asks,
            ..fixed.reply
        },
        notes: fixed.notes,
        code_fixed: !found.is_empty(),
        ungrounded_after_repair: left,
    })
}

/// Rendered-safe text, and the subject set by code: `Re: <incoming subject>` on email, none elsewhere.
fn clean(reply: &Reply, job: &Job) -> Reply {
    let subject = if register_for(&job.channel).subject {
        let incoming = job
            .incoming
            .as_ref()
            .map_or(String::new(), Incoming::reply_subject);
        if incoming.is_empty() {
            sanitize(&reply.subject)
        } else {
            incoming
        }
    } else {
        String::new()
    };
    let alt = reply
        .alt_shorter
        .as_deref()
        .map(sanitize)
        .filter(|a| !a.is_empty());
    Reply {
        subject,
        draft: sanitize(&reply.draft),
        alt_shorter: alt,
        asks: reply.asks.clone(),
    }
}

fn repair(messages: &[Value], data: Option<&Value>, violations: &[String]) -> Vec<Value> {
    let previous = data.map_or("(not a JSON object)".to_string(), Value::to_string);
    let mut out = messages.to_vec();
    out.push(json!({"role": "user", "content": prompts::repair_user(&previous, violations)}));
    out
}
