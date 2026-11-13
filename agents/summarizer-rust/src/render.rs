//! Pure: the reply, in fixed sections, from checked items only. Re-render views cost no LLM call.

use std::collections::BTreeMap;

use crate::checks::clean_text;
use crate::items::{Item, NOT_STATED, Summary};
use crate::util::thousands;

pub const EMPTY: &str = "None recorded in the text.";

/// Which part of a summary to show.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum View {
    Default,
    Actions,
    Decisions,
    Tldr,
    Table,
    Owners,
}

pub fn render(
    summary: &Summary,
    view: View,
    with_sources: bool,
    words: usize,
    parts: usize,
    extra_notes: &[String],
) -> String {
    let body: Vec<String> = match view {
        View::Table => vec![combined_table(summary)],
        View::Owners => vec![by_owner(&summary.actions)],
        View::Actions => vec![section("actions", summary, with_sources)],
        View::Decisions => vec![section("decisions", summary, with_sources)],
        View::Tldr => vec![section("tldr", summary, with_sources)],
        View::Default => ["tldr", "decisions", "actions"]
            .iter()
            .map(|name| section(name, summary, with_sources))
            .collect(),
    };
    let foot = footer(summary, words, parts, extra_notes);
    body.into_iter()
        .chain(std::iter::once(foot))
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}

pub fn footer(summary: &Summary, words: usize, parts: usize, extra_notes: &[String]) -> String {
    let mut lines = Vec::new();
    if words > 0 {
        let unit = if parts == 1 { "part" } else { "parts" };
        lines.push(format!(
            "Summarized {} words in {parts} {unit}.",
            thousands(words)
        ));
    }
    if summary.dropped > 0 {
        lines.push(format!(
            "{} left out because I couldn't find them in your text.",
            count(summary.dropped)
        ));
    }
    if summary.capped > 0 {
        lines.push(format!(
            "{} more left out to keep the lists short.",
            count(summary.capped)
        ));
    }
    let mut out = Vec::new();
    if !lines.is_empty() {
        out.push(format!("_({})_", lines.join(" ")));
    }
    out.extend(
        summary
            .notes
            .iter()
            .chain(extra_notes)
            .filter(|n| !n.is_empty())
            .cloned(),
    );
    out.join("\n")
}

fn count(n: usize) -> String {
    if n == 1 {
        format!("{n} item")
    } else {
        format!("{n} items")
    }
}

fn or_empty(lines: Vec<String>) -> String {
    if lines.is_empty() {
        EMPTY.to_string()
    } else {
        lines.join("\n")
    }
}

fn owner_of(item: &Item) -> &str {
    if item.owner.is_empty() {
        NOT_STATED
    } else {
        &item.owner
    }
}

fn due_of(item: &Item) -> &str {
    if item.due.is_empty() { "-" } else { &item.due }
}

fn section(name: &str, summary: &Summary, show_sources: bool) -> String {
    match name {
        "tldr" => {
            let lines = summary
                .tldr
                .iter()
                .map(|i| format!("- {}{}", i.text, quote(i, show_sources)))
                .collect();
            format!("**TL;DR**\n{}", or_empty(lines))
        }
        "decisions" => {
            let lines = summary
                .decisions
                .iter()
                .enumerate()
                .map(|(n, i)| format!("{}. {}{}", n + 1, i.text, quote(i, show_sources)))
                .collect();
            format!("**Decisions**\n{}", or_empty(lines))
        }
        _ => actions(&summary.actions, show_sources),
    }
}

fn actions(items: &[Item], show_sources: bool) -> String {
    if items.is_empty() {
        return format!("**Action items**\n{EMPTY}");
    }
    if show_sources {
        let lines: Vec<String> = items
            .iter()
            .enumerate()
            .map(|(n, i)| {
                let line = format!(
                    "{}. {} (owner: {}; due: {})",
                    n + 1,
                    i.text,
                    owner_of(i),
                    due_of(i)
                );
                line + &quote(i, true)
            })
            .collect();
        return format!("**Action items**\n{}", lines.join("\n"));
    }
    let rows: Vec<String> = items
        .iter()
        .enumerate()
        .map(|(n, i)| {
            format!(
                "| {} | {} | {} | {} |",
                n + 1,
                i.text,
                owner_of(i),
                due_of(i)
            )
        })
        .collect();
    format!(
        "**Action items**\n| # | Action | Owner | Due |\n|---|---|---|---|\n{}",
        rows.join("\n")
    )
}

/// The quote is the user's own text, but images and HTML in it are still never rendered.
fn quote(item: &Item, show: bool) -> String {
    if show {
        format!("\n  > \"{}\"", clean_text(&item.snippet, &item.snippet))
    } else {
        String::new()
    }
}

fn combined_table(summary: &Summary) -> String {
    let mut rows: Vec<String> = summary
        .decisions
        .iter()
        .map(|i| format!("| Decision | {} | - | - |", i.text))
        .collect();
    rows.extend(
        summary
            .actions
            .iter()
            .map(|i| format!("| Action | {} | {} | {} |", i.text, owner_of(i), due_of(i))),
    );
    table_text(rows)
}

/// The combined decisions + actions table from its rows.
pub fn table_text(rows: Vec<String>) -> String {
    if rows.is_empty() {
        return format!("**Decisions and action items**\n{EMPTY}");
    }
    format!(
        "**Decisions and action items**\n| Type | Item | Owner | Due |\n|---|---|---|---|\n{}",
        rows.join("\n")
    )
}

fn by_owner(actions: &[Item]) -> String {
    let rows: Vec<(String, String, String)> = actions
        .iter()
        .map(|i| (i.text.clone(), owner_of(i).to_string(), i.due.clone()))
        .collect();
    owners_text(&rows)
}

/// "Who owns what" from (action, owner, due) rows; a due of "" or "-" is not shown.
pub fn owners_text(rows: &[(String, String, String)]) -> String {
    if rows.is_empty() {
        return format!("**Who owns what**\n{EMPTY}");
    }
    let mut owners: BTreeMap<(bool, String, String), Vec<String>> = BTreeMap::new();
    for (text, owner, due) in rows {
        let owner = if owner.is_empty() {
            NOT_STATED
        } else {
            owner.as_str()
        };
        let key = (owner == NOT_STATED, owner.to_lowercase(), owner.to_string());
        let due = if due.is_empty() || due == "-" {
            String::new()
        } else {
            format!(" (due: {due})")
        };
        owners
            .entry(key)
            .or_default()
            .push(format!("- {text}{due}"));
    }
    let blocks: Vec<String> = owners
        .into_iter()
        .map(|((_, _, owner), lines)| format!("**{owner}**\n{}", lines.join("\n")))
        .collect();
    format!("**Who owns what**\n\n{}", blocks.join("\n\n"))
}
