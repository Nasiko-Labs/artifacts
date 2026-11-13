//! Pure: the chat text for a split, a merge and the follow-ups. MAF and the chat read text only, so every
//! part is under its own `=== PART i/N ===` heading; a reply that would pass CHAT_MAX_CHARS shows part 1 in
//! full and the rest as one line each ("show part i" prints one).

use crate::envelope::fenced;
use crate::merge::MergeResult;
use crate::prompts_out::MERGE_INSTRUCTION;
use crate::replies::{self, thousands};
use crate::split::{Part, SplitResult};

const CHAT_MAX_CHARS: usize = 20_000;
const PREVIEW_WORDS: usize = 8;

pub fn split_reply(result: &SplitResult) -> String {
    let prompts: Vec<&str> = result.parts.iter().map(|p| p.prompt.as_str()).collect();
    let total: usize = prompts.iter().map(|p| p.chars().count()).sum();
    let body = if total > CHAT_MAX_CHARS && prompts.len() > 1 {
        std::iter::once(prompts[0].to_owned())
            .chain(result.parts[1..].iter().map(collapsed))
            .collect::<Vec<_>>()
            .join("\n\n")
    } else {
        prompts.join("\n\n")
    };
    let mut pieces = vec![result.summary(), body];
    if result.parts.len() != 1 {
        pieces.push(format!("Merge instruction: {MERGE_INSTRUCTION}"));
    }
    pieces.extend(result.notes.iter().cloned());
    pieces.push(replies::TOKEN_NOTE.to_owned());
    join(pieces)
}

fn collapsed(part: &Part) -> String {
    format!(
        "PART {}/{} · {} chars · {} (say \"show part {}\")",
        part.n,
        part.of,
        thousands(part.chars as u64),
        part.label,
        part.n
    )
}

pub fn part_list(result: &SplitResult) -> String {
    let mut lines = vec![result.summary()];
    for part in &result.parts {
        let flag = if part.split_mid_unit {
            " · cut mid-unit"
        } else {
            ""
        };
        lines.push(format!(
            "- PART {}/{} · {} chars (~{} tokens) · {}{flag} · starts \"{}\"",
            part.n,
            part.of,
            thousands(part.chars as u64),
            thousands(part.est_tokens as u64),
            part.label,
            preview(&part.prompt)
        ));
    }
    lines.join("\n")
}

pub fn merge_reply(result: &MergeResult) -> String {
    let line = if result.notes.is_empty() {
        "Merge notes: no duplicates, nothing ignored.".to_owned()
    } else {
        format!("Merge notes: {}.", result.notes.join("; "))
    };
    let head = format!(
        "Merged {} results into one with the same schema.",
        result.objects.len()
    );
    let body = result.envelope.as_ref().map(fenced).unwrap_or_default();
    join(vec![head, body, line])
}

pub fn merge_notes(result: &MergeResult) -> String {
    if result.notes.is_empty() {
        return "No merge notes: no duplicates were removed and nothing was ignored.".to_owned();
    }
    std::iter::once("Merge notes:".to_owned())
        .chain(result.notes.iter().map(|n| format!("- {n}")))
        .collect::<Vec<_>>()
        .join("\n")
}

fn preview(prompt: &str) -> String {
    let body = prompt.split_once("<<<TEXT>>>\n").map_or(prompt, |(_, b)| b);
    let words: Vec<&str> = body.split_whitespace().collect();
    let shown = words
        .iter()
        .take(PREVIEW_WORDS)
        .copied()
        .collect::<Vec<_>>()
        .join(" ");
    if words.len() > PREVIEW_WORDS {
        format!("{shown}…")
    } else {
        shown
    }
}

fn join(pieces: Vec<String>) -> String {
    pieces
        .into_iter()
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}
