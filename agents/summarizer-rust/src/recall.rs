//! Pure: what an earlier turn of this conversation still gives a follow-up, read from the platform's
//! history block (this agent stores nothing). Two things can be recalled:
//!
//! - the most recent pasted source (a user turn with at least MIN_SOURCE_WORDS words of text);
//! - the most recent summary reply (an assistant turn rendered by `render.rs`), cut into its sections so
//!   a view ("only the action items", "as a table", "who owns what") is rebuilt from it in code, with
//!   zero LLM calls.
//!
//! The history is a budgeted slice, so either may be missing: callers then say so and ask for a
//! re-paste. Nothing here invents content: every view is made of the earlier reply's own lines.

use std::sync::LazyLock;

use regex::Regex;

use crate::history::{ASSISTANT, Turn, USER, latest, texts};
use crate::inputs::{MIN_SOURCE_WORDS, split_request, word_count};
use crate::prompts;
use crate::render::{EMPTY, View, owners_text, table_text};
use crate::util::{group, lines, re};

const SECTIONS: [(View, &str); 3] = [
    (View::Tldr, "**TL;DR**"),
    (View::Decisions, "**Decisions**"),
    (View::Actions, "**Action items**"),
];
static QUOTE_LINE: LazyLock<Regex> = LazyLock::new(|| re(r#"^\s+> ""#));
static NUMBERED: LazyLock<Regex> = LazyLock::new(|| re(r"^\d+\. (?P<text>.+)$"));
static LISTED_ACTION: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\d+\. (?P<text>.+) \(owner: (?P<owner>.+); due: (?P<due>.+)\)$"));

/// (instruction, source) of the most recent user turn that pasted a text to summarize, else ("", "").
pub fn source(turns: &[Turn]) -> (String, String) {
    for text in texts(turns, USER) {
        let (instruction, pasted) = split_request(text);
        if word_count(&pasted) >= MIN_SOURCE_WORDS {
            return (
                prompts::strip_markers(&instruction),
                prompts::strip_markers(&pasted),
            );
        }
    }
    (String::new(), String::new())
}

/// The most recent assistant reply that is a summary (or a section of one), else "".
pub fn summary_reply(turns: &[Turn]) -> &str {
    latest(turns, ASSISTANT, |t| {
        SECTIONS.iter().any(|(_, head)| !block(t, head).is_empty())
    })
}

/// `view` rebuilt from the most recent summary reply that has what it needs, or None.
pub fn view_from_history(turns: &[Turn], view: View, with_sources: bool) -> Option<String> {
    texts(turns, ASSISTANT)
        .into_iter()
        .find_map(|reply| view_from_reply(reply, view, with_sources))
}

pub fn view_from_reply(reply: &str, view: View, with_sources: bool) -> Option<String> {
    match view {
        View::Table => return table(reply),
        View::Owners => return owners(reply),
        _ => {}
    }
    let heads: Vec<&str> = SECTIONS
        .iter()
        .filter(|(v, _)| view == View::Default || *v == view)
        .map(|(_, head)| *head)
        .collect();
    let blocks: Vec<String> = heads.iter().map(|head| block(reply, head)).collect();
    if blocks.first().is_none_or(String::is_empty) {
        return None;
    }
    let quoted = blocks
        .iter()
        .filter(|b| !b.is_empty() && !b.contains(EMPTY))
        .all(|b| b.contains("> \""));
    if with_sources && !quoted {
        return None;
    }
    let shown: Vec<String> = blocks
        .into_iter()
        .filter(|b| !b.is_empty())
        .map(|b| if with_sources { b } else { without_quotes(&b) })
        .collect();
    Some(shown.join("\n\n"))
}

fn block(reply: &str, head: &str) -> String {
    reply
        .split("\n\n")
        .map(str::trim)
        .find(|b| b.starts_with(head))
        .unwrap_or("")
        .to_string()
}

fn without_quotes(block: &str) -> String {
    lines(block)
        .into_iter()
        .filter(|l| !QUOTE_LINE.is_match(l))
        .collect::<Vec<_>>()
        .join("\n")
}

fn decisions(reply: &str) -> Option<Vec<String>> {
    let block = block(reply, "**Decisions**");
    if block.is_empty() {
        return None;
    }
    Some(
        lines(&block)
            .into_iter()
            .skip(1)
            .filter_map(|l| {
                NUMBERED
                    .captures(l.trim())
                    .map(|c| group(&c, "text").to_string())
            })
            .collect(),
    )
}

/// (action, owner, due) rows from the actions block in either rendered form (table or listed).
fn actions(reply: &str) -> Option<Vec<(String, String, String)>> {
    let block = block(reply, "**Action items**");
    if block.is_empty() {
        return None;
    }
    let mut rows = Vec::new();
    for line in lines(&block).into_iter().skip(1) {
        let line = line.trim();
        let cells: Vec<&str> = line.trim_matches('|').split('|').map(str::trim).collect();
        let numbered = cells
            .first()
            .is_some_and(|c| !c.is_empty() && c.chars().all(|ch| ch.is_ascii_digit()));
        if line.starts_with('|') && cells.len() == 4 && numbered {
            rows.push((
                cells[1].to_string(),
                cells[2].to_string(),
                cells[3].to_string(),
            ));
        } else if let Some(c) = LISTED_ACTION.captures(line) {
            let cell = |name| group(&c, name).to_string();
            rows.push((cell("text"), cell("owner"), cell("due")));
        }
    }
    Some(rows)
}

fn table(reply: &str) -> Option<String> {
    let decisions = decisions(reply)?;
    let actions = actions(reply)?;
    let mut rows: Vec<String> = decisions
        .iter()
        .map(|d| format!("| Decision | {d} | - | - |"))
        .collect();
    rows.extend(
        actions
            .iter()
            .map(|(text, owner, due)| format!("| Action | {text} | {owner} | {due} |")),
    );
    Some(table_text(rows))
}

fn owners(reply: &str) -> Option<String> {
    actions(reply).map(|rows| owners_text(&rows))
}
