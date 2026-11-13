//! Pure: tone notes, confidence and the output envelope, all computed in code from the measured style and
//! the final checks (never from the model's own words).

use serde_json::{Value, json};

use crate::draft::Reply;
use crate::grounding::placeholders;
use crate::register::register_for;
use crate::style::{Metrics, NONE, formality_name, per_words};
use crate::textstats as ts;
use crate::tone_check::{self, Target, Violation};
use crate::util::upper_first;

const LENGTH_CHECKS: &[&str] = &["length", "cap"];
const NOTED: &[&str] = &[
    "length",
    "greeting",
    "sign-off",
    "contractions",
    "exclamations",
    "emoji",
];

pub fn final_violations(reply: &Reply, target: &Target) -> Vec<Violation> {
    tone_check::violations(&reply.draft, target)
}

fn plural(n: usize) -> &'static str {
    if n == 1 { "" } else { "s" }
}

pub fn tone_notes(m: &Metrics, target: &Target, reply: &Reply) -> String {
    let left = final_violations(reply, target);
    if !m.measured() {
        return concat!(
            "Neutral default tone (no past replies given): formality 3/5, opens with 'Hi {first},'. ",
            "Paste 1-3 of your past replies and I'll match your style."
        )
        .into();
    }
    let mut style = vec![
        format!(
            "{} ({}/5, approx.)",
            formality_name(m.formality),
            m.formality
        ),
        format!("about {} words", m.avg_words),
    ];
    if m.greeting != NONE {
        style.push(format!("opens with '{}'", m.greeting));
    }
    if m.sign_off != NONE {
        style.push(format!("signs '{}'", m.sign_off.replace('\n', " ")));
    }
    style.push(if m.emoji { "emoji" } else { "no emoji" }.into());
    style.push(contractions(m));
    let lead = format!(
        "Matched to your {} example{}: {}.",
        m.examples,
        plural(m.examples),
        style.join(", ")
    );
    if target.relaxed {
        return format!(
            "{lead} You asked for a different style this turn, so it was a starting point, not a rule."
        );
    }
    let failed: Vec<&str> = left.iter().map(|v| v.check).collect();
    let checked: Vec<String> = NOTED
        .iter()
        .map(|name| {
            let state = if failed.contains(name) { "off" } else { "ok" };
            format!("{name} {state}")
        })
        .collect();
    let mut notes = vec![lead, format!("Checked: {}.", checked.join(", "))];
    let words = ts::word_count(&reply.draft);
    if failed.contains(&"length") {
        let side = if words > m.avg_words {
            "Longer"
        } else {
            "Shorter"
        };
        notes.push(format!(
            "{side} than your examples: {words} words vs ~{}.",
            m.avg_words
        ));
    }
    notes.extend(
        left.iter()
            .filter(|v| v.check != "length")
            .map(|v| format!("{}.", upper_first(&v.message))),
    );
    notes.join(" ")
}

fn contractions(m: &Metrics) -> String {
    if m.contraction_rate == 0.0 {
        return "no contractions".into();
    }
    format!("1 contraction per {} words", per_words(m.contraction_rate))
}

pub fn confidence(
    m: &Metrics,
    reply: &Reply,
    target: &Target,
    code_fixed: bool,
    ungrounded_after_repair: bool,
) -> &'static str {
    let left: Vec<&str> = final_violations(reply, target)
        .iter()
        .map(|v| v.check)
        .collect();
    if !m.measured() || ungrounded_after_repair || left.iter().any(|c| LENGTH_CHECKS.contains(c)) {
        return "low";
    }
    if m.examples >= 2
        && !code_fixed
        && left.is_empty()
        && placeholders(&all_text(reply)).is_empty()
    {
        return "high";
    }
    "medium"
}

fn all_text(reply: &Reply) -> String {
    [
        reply.subject.as_str(),
        reply.draft.as_str(),
        reply.alt_shorter.as_deref().unwrap_or(""),
    ]
    .join("\n")
}

pub fn summary(reply_to: &str, channel: &str, m: &Metrics) -> String {
    let who = if reply_to.is_empty() {
        "the sender"
    } else {
        reply_to
    };
    let label = register_for(channel)
        .label
        .split_whitespace()
        .next()
        .unwrap_or("")
        .to_lowercase();
    if !m.measured() {
        return format!("Reply to {who} ({label}), neutral default tone.");
    }
    format!(
        "Reply to {who} ({label}), tone matched to {} example{}.",
        m.examples,
        plural(m.examples)
    )
}

pub fn build(
    reply: &Reply,
    channel: &str,
    sender: &str,
    m: &Metrics,
    notes: &str,
    level: &str,
) -> Value {
    let mut items =
        vec![json!({"label": "draft", "text": reply.draft, "words": ts::word_count(&reply.draft)})];
    if let Some(alt) = &reply.alt_shorter {
        items.push(json!({"label": "alt_shorter", "text": alt, "words": ts::word_count(alt)}));
    }
    let asks: Vec<Value> = reply
        .asks
        .iter()
        .map(|a| json!({"ask": a.ask, "quote": a.quote}))
        .collect();
    json!({
        "summary": summary(sender, channel, m),
        "items": items,
        "draft": reply.draft,
        "alt_shorter": reply.alt_shorter,
        "tone_notes": notes,
        "subject": (!reply.subject.is_empty()).then_some(&reply.subject),
        "channel": channel,
        "reply_to": (!sender.is_empty()).then_some(sender),
        "placeholders": placeholders(&all_text(reply)),
        "asks_unaddressed": asks,
        "confidence": level,
        "needs_human": true,
    })
}

pub fn fenced(envelope: &Value) -> String {
    let body = serde_json::to_string_pretty(envelope).unwrap_or_else(|_| "{}".into());
    format!("```json\n{body}\n```")
}
