//! Pure: what an earlier turn of this conversation still gives a follow-up, read from the platform's
//! history block (this agent stores nothing). Three things can be recalled:
//!
//! - the most recent pasted transcript (a user turn with at least MIN_SOURCE_WORDS words of meeting text);
//! - the most recent notes reply (an assistant turn rendered by `render.rs`), cut into its sections so a
//!   view ("only decisions", "only mine") is served from it in code, with zero LLM calls;
//! - the user's name ("I'm Raj"), best-effort.
//!
//! The history is a budgeted slice, so any of these may be missing: callers then say so and ask for a
//! re-paste. Nothing here invents content: a view is a subset of the earlier reply's own lines.

use crate::history::{ASSISTANT, HistoryTurn, USER, latest, texts};
use crate::inputs::{
    MIN_SOURCE_WORDS, from_envelope, my_name_in, name_only, split_request, word_count,
};
use crate::items::NOT_STATED;
use crate::prompts;
use crate::render::View;
use crate::util::lines;

const SECTIONS: [(View, &str); 4] = [
    (View::Notes, "**Notes**"),
    (View::Decisions, "**Decisions**"),
    (View::Actions, "**Action items**"),
    (View::Followups, "**Follow-ups**"),
];

/// (instruction, transcript) of one message's text, fence markers stripped from both.
pub fn source_of_text(text: &str) -> (String, String) {
    if let Some(enveloped) = from_envelope(text) {
        return (String::new(), prompts::strip_markers(&enveloped));
    }
    let (instruction, source) = split_request(text);
    (
        prompts::strip_markers(&instruction),
        prompts::strip_markers(&source),
    )
}

/// (instruction, transcript) of the most recent user turn that pasted a meeting, else ("", "").
pub fn transcript(turns: &[HistoryTurn]) -> (String, String) {
    texts(turns, USER)
        .into_iter()
        .map(source_of_text)
        .find(|(_, source)| word_count(source) >= MIN_SOURCE_WORDS)
        .unwrap_or_default()
}

/// The most recent "I'm Raj" the user said in a short message or a request line, else "".
pub fn my_name(turns: &[HistoryTurn]) -> String {
    for text in texts(turns, USER) {
        let (instruction, source) = source_of_text(text);
        let said = name_only(text);
        if !said.is_empty() {
            return said;
        }
        let line = if word_count(&source) >= MIN_SOURCE_WORDS {
            instruction.as_str()
        } else {
            text
        };
        let said = my_name_in(line);
        if !said.is_empty() {
            return said;
        }
    }
    String::new()
}

/// The most recent assistant reply that is a set of meeting notes, else "".
pub fn notes_reply(turns: &[HistoryTurn]) -> &str {
    latest(turns, ASSISTANT, |t| {
        SECTIONS.iter().any(|(_, head)| !block(t, head).is_empty())
    })
}

/// `view` cut from the most recent notes reply that has what it needs, or None.
pub fn view_from_history(turns: &[HistoryTurn], view: View, name: &str) -> Option<String> {
    let servable = matches!(
        view,
        View::Notes
            | View::Decisions
            | View::Actions
            | View::Followups
            | View::Default
            | View::Mine
    );
    if !servable {
        return None;
    }
    texts(turns, ASSISTANT)
        .into_iter()
        .find_map(|reply| view_from_reply(reply, view, name))
}

pub fn view_from_reply(reply: &str, view: View, name: &str) -> Option<String> {
    let head = header(reply);
    match view {
        View::Default => (!head.is_empty() && !block(reply, "**Notes**").is_empty())
            .then(|| reply.trim().to_string()),
        View::Mine => mine(reply, &head, name),
        _ => {
            let start = SECTIONS.iter().find(|(v, _)| *v == view)?.1;
            let found = block(reply, start);
            if found.is_empty() {
                return None;
            }
            Some(join_present(&[head.as_str(), found.as_str()]))
        }
    }
}

fn join_present(parts: &[&str]) -> String {
    parts
        .iter()
        .filter(|p| !p.is_empty())
        .copied()
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn blocks(reply: &str) -> Vec<&str> {
    reply
        .split("\n\n")
        .map(str::trim)
        .filter(|b| !b.is_empty())
        .collect()
}

fn header(reply: &str) -> String {
    blocks(reply)
        .first()
        .filter(|b| b.starts_with("**") && b.contains("Attendees:"))
        .map_or(String::new(), |b| b.to_string())
}

fn block(reply: &str, start: &str) -> String {
    blocks(reply)
        .into_iter()
        .find(|b| b.starts_with(start) && !b.starts_with("**Decisions and"))
        .unwrap_or("")
        .to_string()
}

fn mine(reply: &str, head: &str, name: &str) -> Option<String> {
    let tables: Vec<(String, &str)> = [
        ("**Action items**", "action"),
        ("**Follow-ups**", "follow-up"),
    ]
    .into_iter()
    .map(|(start, kind)| (block(reply, start), kind))
    .filter(|(b, _)| !b.is_empty())
    .collect();
    if name.trim().is_empty() || tables.is_empty() {
        return None;
    }
    let mut numbered: Vec<String> = tables
        .iter()
        .flat_map(|(table, kind)| rows(table).into_iter().map(move |cells| (cells, *kind)))
        .filter(|(cells, _)| is_name(&cells[1], name))
        .enumerate()
        .map(|(n, (cells, kind))| format!("{}. {}", n + 1, mine_line(&cells, kind)))
        .collect();
    if numbered.is_empty() {
        numbered.push("None recorded.".to_string());
    }
    let yours = format!("**Yours ({name})**\n{}", numbered.join("\n"));
    Some(join_present(&[head, yours.as_str()]))
}

fn rows(table: &str) -> Vec<Vec<String>> {
    lines(table)
        .into_iter()
        .filter_map(|line| {
            let line = line.trim();
            let cells: Vec<String> = line
                .trim_matches('|')
                .split('|')
                .map(|c| c.trim().to_string())
                .collect();
            let numbered = cells
                .first()
                .is_some_and(|c| !c.is_empty() && c.chars().all(|ch| ch.is_ascii_digit()));
            (line.starts_with('|') && cells.len() >= 4 && numbered).then_some(cells)
        })
        .collect()
}

fn owner_of(cell: &str) -> &str {
    cell.strip_suffix('*').unwrap_or(cell).trim()
}

fn mine_line(cells: &[String], kind: &str) -> String {
    let owner = owner_of(&cells[1]);
    let owner = if owner.is_empty() { NOT_STATED } else { owner };
    let due = if cells[3].is_empty() { "-" } else { &cells[3] };
    format!("{} ({kind}; owner: {owner}; due: {due})", cells[2])
}

fn is_name(owner_cell: &str, name: &str) -> bool {
    let owner = owner_of(owner_cell).to_lowercase();
    let wanted = name.trim().to_lowercase();
    if owner.is_empty() || owner == NOT_STATED.to_lowercase() {
        return false;
    }
    let first = |s: &str| s.split_whitespace().next().unwrap_or("").to_string();
    owner == wanted || (!first(&wanted).is_empty() && first(&owner) == first(&wanted))
}
