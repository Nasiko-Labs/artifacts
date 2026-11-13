//! Pure: the reply text. Rendered by code: markdown images and raw HTML are removed, and a markdown link is
//! shown as plain text with its address (which the grounding check has vetted).

use std::sync::LazyLock;

use serde_json::Value;

use crate::style::{Metrics, NONE};
use crate::util::{Regex, re, replace_all};

static IMAGE: LazyLock<Regex> = LazyLock::new(|| re(r"!\[[^\]]*\]\([^)]*\)"));
static HTML: LazyLock<Regex> = LazyLock::new(|| re(r"(?s)</?[A-Za-z][^>]*>|<!--.*?-->"));
static MD_LINK: LazyLock<Regex> = LazyLock::new(|| re(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)"));
static RUNS: LazyLock<Regex> = LazyLock::new(|| re(r"[ \t]{2,}"));
pub const FOOTER: &str = concat!(
    "_Draft only. I don't send messages; copy it when you're happy. Say \"shorter\", \"warmer\" or ",
    "\"say no instead\"._"
);
pub const EXAMPLES_HINT: &str = "Paste 1-3 of your past replies and I'll match your style.";

pub fn sanitize(text: &str) -> String {
    let out = replace_all(&IMAGE, text, "").into_owned();
    let out = replace_all(&HTML, &out, "").into_owned();
    let out = replace_all(&MD_LINK, &out, "$1 ($2)").into_owned();
    replace_all(&RUNS, &out, " ").trim().to_string()
}

fn str_of<'a>(envelope: &'a Value, key: &str) -> &'a str {
    envelope.get(key).and_then(Value::as_str).unwrap_or("")
}

fn plural(n: usize) -> &'static str {
    if n == 1 { "" } else { "s" }
}

pub fn reply_text(envelope: &Value, metrics: &Metrics, notes: &[String]) -> String {
    let who = match str_of(envelope, "reply_to") {
        "" => "the sender",
        name => name,
    };
    let source = if metrics.measured() {
        format!(
            "tone from your {} example{}",
            metrics.examples,
            plural(metrics.examples)
        )
    } else {
        "neutral default tone".to_string()
    };
    let mut lines = vec![format!(
        "**Reply to {who} ({})** · {source}",
        str_of(envelope, "channel")
    )];
    let subject = str_of(envelope, "subject");
    if !subject.is_empty() {
        lines.push(format!("**Subject:** {}", sanitize(subject)));
    }
    lines.extend([
        String::new(),
        sanitize(str_of(envelope, "draft")),
        String::new(),
    ]);
    let alt = str_of(envelope, "alt_shorter");
    if !alt.is_empty() {
        lines.push(format!("**Shorter:** {}", sanitize(alt)));
    }
    lines.push(format!(
        "**Tone notes:** {}",
        str_of(envelope, "tone_notes")
    ));
    let mut tail: Vec<String> = Vec::new();
    let placeholders: Vec<&str> = envelope
        .get("placeholders")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    if !placeholders.is_empty() {
        tail.push(format!(
            "Fill in before sending: {}",
            placeholders.join(", ")
        ));
    }
    let quotes: Vec<String> = envelope
        .get("asks_unaddressed")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|a| format!("\"{}\"", str_of(a, "quote")))
                .collect()
        })
        .unwrap_or_default();
    if !quotes.is_empty() {
        tail.push(format!("They also asked: {}", quotes.join("; ")));
    }
    tail.extend(notes.iter().cloned());
    tail.push(FOOTER.to_string());
    format!("{}\n\n{}", lines.join("\n"), tail.join("\n"))
}

pub fn tone_saved(metrics: &Metrics, has_draft: bool) -> String {
    let mut lead = format!(
        "Got your style from {} example{}: about {} words, formality {}/5 (approx.)",
        metrics.examples,
        plural(metrics.examples),
        metrics.avg_words,
        metrics.formality
    );
    if metrics.greeting != NONE {
        lead.push_str(&format!(", opens with '{}'", metrics.greeting));
    }
    if metrics.sign_off != NONE {
        lead.push_str(&format!(
            ", signs '{}'",
            metrics.sign_off.replace('\n', " ")
        ));
    }
    let follow = if has_draft {
        "Say 'redo' to rewrite the current reply in it."
    } else {
        "Now paste the message you're replying to and tell me what to say back."
    };
    format!("{lead}. {follow}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_drops_images_html_and_shows_link_addresses() {
        assert_eq!(
            sanitize("Hi ![x](http://e/p.png) <b>no</b>  see [docs](https://d.example)"),
            "Hi no see docs (https://d.example)"
        );
    }
}
