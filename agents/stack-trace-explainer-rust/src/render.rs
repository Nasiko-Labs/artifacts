//! Pure: the markdown reply (or the fenced JSON envelope), built by code from the checked envelope.
//! Markdown only: no raw HTML, no images, and URLs only where the user's own paste had them.

use serde_json::Value;

use crate::envelope::{Envelope, Item, ItemChecks};
use crate::prompts;

fn fence_lang(language: &str) -> &'static str {
    match language {
        "python" => "python",
        "javascript" => "javascript",
        "java" => "java",
        "go" => "go",
        "rust" => "rust",
        "ruby" => "ruby",
        "csharp" => "csharp",
        _ => "",
    }
}

fn origin_text(origin: &str) -> &str {
    match origin {
        "app" => "your code",
        "library" => "library code",
        "none" => "unknown",
        other => other,
    }
}

pub fn code(text: &str) -> String {
    if text.contains('`') {
        format!("`` {text} ``")
    } else {
        format!("`{text}`")
    }
}

pub fn render_json(envelope: &Value) -> String {
    let body = serde_json::to_string_pretty(envelope).unwrap_or_else(|_| "{}".into());
    format!("```json\n{body}\n```")
}

pub fn render(envelope: &Envelope, notes: &[String]) -> String {
    let items = &envelope.items;
    let sections: Vec<String> = items
        .iter()
        .enumerate()
        .map(|(n, item)| {
            let heading = if items.len() > 1 {
                let role = if n == 0 { " (primary)" } else { "" };
                format!(
                    "### Error {} of the pasted traces{role}\n",
                    item.trace_index
                )
            } else {
                String::new()
            };
            heading + &render_item(item)
        })
        .collect();
    let mut footer = footer(envelope);
    footer.extend(notes.iter().cloned());
    footer.push(prompts::READ_ONLY_NOTE.to_string());
    format!("{}\n\n{}", sections.join("\n\n---\n\n"), footer.join("\n"))
}

pub fn render_item(item: &Item) -> String {
    let message = if item.message.is_empty() {
        String::new()
    } else {
        format!(" — {}", item.message)
    };
    let mut lines = vec![format!(
        "**{}** ({}){message}",
        item.exception, item.language
    )];
    if !item.chain.is_empty() {
        let chain: Vec<String> = item.chain.iter().map(|c| code(c)).collect();
        lines.push(format!(
            "**Chain (root cause first):** {}",
            chain.join(" → ")
        ));
    }
    lines.push(look_at(item));
    if let Some(reason) = &item.frame_reason {
        lines.push(format!("_Chosen over the innermost app frame: {reason}_"));
    }
    let mut cause = item.likely_cause.clone();
    if item.evidence.is_empty() {
        cause.push_str(" _(unverified: no line from your paste backs this)_");
    }
    lines.push(format!("\n**Probable cause.** {cause}"));
    for ev in &item.evidence {
        let place = if ev.source == "code" {
            "your snippet"
        } else {
            "the trace"
        };
        lines.push(format!("> {}  ({place})", code(&ev.quote)));
    }
    if !item.fix_suggestion.is_empty() {
        lines.push(format!("\n**Fix.** {}", item.fix_suggestion));
    }
    if let Some(fix) = &item.fix_code {
        lines.push(format!(
            "```{}\n{}\n```",
            fence_lang(&item.language),
            fix.trim_end()
        ));
    }
    if !item.how_to_verify.is_empty() {
        lines.push("\n**How to verify.**".to_string());
        lines.extend(
            item.how_to_verify
                .iter()
                .enumerate()
                .map(|(i, s)| format!("{}. {s}", i + 1)),
        );
    }
    if let Some(need) = &item.need_code {
        lines.push(format!(
            "\nTo be sure, paste lines {} of {}.",
            need.lines,
            code(&need.file)
        ));
    }
    if let Some(notes) = &item.confidence_notes {
        lines.push(format!("\n_What would change this: {notes}_"));
    }
    lines.push(format!(
        "\n_Confidence: {}._{}",
        item.confidence,
        checks_note(&item.checks)
    ));
    lines.join("\n")
}

fn look_at(item: &Item) -> String {
    let Some(detail) = &item.location_detail else {
        return "**Look at:** no stack frame was found in the paste, so the location is unknown."
            .to_string();
    };
    let place = code(&format!("{}:{}", detail.file, detail.line));
    let func = detail
        .function
        .as_ref()
        .map_or(String::new(), |f| format!(" in {}", code(f)));
    let origin = origin_text(&detail.origin);
    if detail.origin == "library" {
        return format!(
            "**Look at:** {place}{func} ({origin}: the error surfaces inside a library; check how your code calls it)"
        );
    }
    let source = if detail.from == "code" {
        "line shown from your snippet"
    } else {
        "from the trace; no code pasted for it"
    };
    format!("**Look at:** {place}{func} ({origin}; {source})")
}

fn checks_note(checks: &ItemChecks) -> String {
    let mut parts = Vec::new();
    if checks.quotes_removed > 0 {
        parts.push(format!(
            "removed {} quote(s) not found in your paste",
            checks.quotes_removed
        ));
    }
    if checks.sentences_removed > 0 {
        parts.push(format!(
            "removed {} sentence(s) citing something not in your paste",
            checks.sentences_removed
        ));
    }
    if checks.fix_code_dropped {
        parts.push("dropped a code fix that failed the checks".to_string());
    }
    if checks.repairs > 0 {
        parts.push(format!(
            "one repair pass fixed {} problem(s)",
            checks.repaired_problems
        ));
    }
    if parts.is_empty() {
        String::new()
    } else {
        format!(" Checks: {}.", parts.join("; "))
    }
}

fn footer(envelope: &Envelope) -> Vec<String> {
    let mut out = Vec::new();
    if envelope.skipped_traces > 0 {
        out.push(format!(
            "_First {} trace(s) skipped: I explain the last 3 in one message._",
            envelope.skipped_traces
        ));
    }
    if !envelope.unexplained_traces.is_empty() {
        let numbers: Vec<String> = envelope
            .unexplained_traces
            .iter()
            .map(usize::to_string)
            .collect();
        out.push(format!(
            "_Not explained this turn (no model call left or no answer): trace {}. Paste it alone._",
            numbers.join(", ")
        ));
    }
    out
}

pub fn render_followup(answer: &str, removed: usize) -> String {
    let note = if removed > 0 {
        format!("\n\n_Removed {removed} sentence(s) citing something not in your paste._")
    } else {
        String::new()
    };
    format!("{answer}{note}\n\n{}", prompts::READ_ONLY_NOTE)
}
