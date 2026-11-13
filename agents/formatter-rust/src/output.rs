//! Pure: the chat text of a rendering. MAF reads text only, so the text part is `formatted` itself (the
//! Markdown as is; Slack JSON and HTML in fenced blocks, so raw HTML is never injected into the chat page)
//! plus a short status line; "as json" gives the envelope instead.

use serde_json::Value;

use crate::envelope::Rendering;
use crate::escape;
use crate::pyjson;

#[derive(Debug, Clone, PartialEq)]
pub struct Reply {
    pub text: String,
    pub envelope: Option<Value>,
}

/// A fenced block that cannot be closed early by backticks inside `body`.
pub fn fence(lang: &str, body: &str) -> String {
    let longest = escape::longest_backtick_run(body, 3).max(2);
    let ticks = "`".repeat(longest + 1);
    format!("{ticks}{lang}\n{body}\n{ticks}")
}

pub fn reply(rendering: &Rendering, as_json: bool, plain_version: bool) -> Reply {
    let envelope = rendering.to_envelope();
    if as_json {
        return Reply {
            text: fence("json", &pyjson::pretty(&envelope)),
            envelope: Some(envelope),
        };
    }
    if let Some(plain) = rendering.plain_text.as_deref().filter(|_| plain_version) {
        let mut parts = vec![format!("Plain-text version:\n\n{}", fence("text", plain))];
        parts.extend(notes(rendering));
        parts.push(status(rendering, true));
        return Reply {
            text: join(parts),
            envelope: Some(envelope),
        };
    }
    let body = if rendering.lang.is_empty() {
        rendering.formatted.clone()
    } else {
        fence(rendering.lang, &rendering.formatted)
    };
    let subject = rendering
        .subject
        .as_deref()
        .map(|s| format!("Suggested subject: {}", escape::md_inline(s)))
        .unwrap_or_default();
    let mut parts = vec![subject, body];
    parts.extend(notes(rendering));
    parts.push(status(rendering, false));
    Reply {
        text: join(parts),
        envelope: Some(envelope),
    }
}

fn notes(rendering: &Rendering) -> Vec<String> {
    rendering
        .notes
        .iter()
        .map(|n| format!("_{}_", escape::md_inline(n)))
        .collect()
}

fn status(rendering: &Rendering, plain_version: bool) -> String {
    let cut = rendering.stats.cut;
    match rendering.target {
        "slack" => format!(
            "_Rendered as Slack blocks: {} blocks, {cut} cut. Say \"as email\" or \"as markdown\"._",
            rendering.stats.blocks
        ),
        "email" => {
            let ask = if plain_version {
                "\"as email\" for the HTML"
            } else {
                "\"show the plain-text version\""
            };
            format!(
                "_Rendered as email HTML with a plain-text version. Say {ask}, \"as slack\" or \"as markdown\"._"
            )
        }
        _ => {
            let sections = rendering.items.len();
            let s = if sections == 1 { "" } else { "s" };
            format!(
                "_Rendered as Markdown: {sections} section{s}, {cut} shortened. Say \"as slack\" or \"as email\"._"
            )
        }
    }
}

fn join(parts: Vec<String>) -> String {
    parts
        .into_iter()
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fence_outgrows_backtick_runs_inside() {
        assert_eq!(fence("json", "{}"), "```json\n{}\n```");
        assert_eq!(fence("", "a ```` b"), "`````\na ```` b\n`````");
    }
}
