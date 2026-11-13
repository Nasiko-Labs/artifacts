//! Pure: the reply, in fixed sections, from checked items and the parse only. Views cost no LLM call.

use serde_json::Value;

use crate::attendees::NOT_IDENTIFIED;
use crate::checks::clean_text;
use crate::envelope::{Meeting, mine_only};
use crate::items::{Action, Kind, NOT_STATED, Notes};

pub const EMPTY: &str = "None recorded.";

/// What a reply shows: the full notes or one view of them.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum View {
    Default,
    Decisions,
    Actions,
    Followups,
    Mine,
    Sources,
    Table,
    Notes,
    Json,
}

pub fn header(meeting: &Meeting) -> String {
    let cleaned = clean_text(&meeting.title, &meeting.title);
    let title = if cleaned.is_empty() {
        "Meeting notes"
    } else {
        &cleaned
    };
    let minutes = meeting.minutes();
    let mut extra: Vec<String> = Vec::new();
    if !meeting.date.is_empty() {
        extra.push(meeting.date.clone());
    }
    if minutes != 0 {
        extra.push(format!("{minutes} min"));
    }
    let when = if extra.is_empty() {
        String::new()
    } else {
        format!(" ({})", extra.join(", "))
    };
    format!("**{title}**{when} Attendees: {}", attendee_line(meeting))
}

pub fn attendee_line(meeting: &Meeting) -> String {
    if meeting.attendees.is_empty() {
        return NOT_IDENTIFIED.to_string();
    }
    meeting
        .attendees
        .iter()
        .map(|a| format!("{} ({}%)", a.name, a.share))
        .collect::<Vec<_>>()
        .join(", ")
}

pub fn cite(turn_ids: &[String], meeting: &Meeting) -> String {
    let time = turn_ids
        .first()
        .and_then(|id| meeting.turn(id))
        .map_or("", |t| t.time.as_str());
    let when = if time.is_empty() {
        String::new()
    } else {
        format!(", {time}")
    };
    format!("[{}{when}]", turn_ids.join(", "))
}

/// The envelope's `notes`: attendees and topics.
pub fn notes_markdown(notes: &Notes, meeting: &Meeting) -> String {
    let mut people: Vec<String> = meeting
        .attendees
        .iter()
        .map(|a| format!("- {}: {} turns ({}%)", a.name, a.turns, a.share))
        .collect();
    if people.is_empty() {
        people.push(format!("- {NOT_IDENTIFIED}"));
    }
    format!(
        "## Attendees\n{}\n\n## Topics\n{}",
        people.join("\n"),
        or_empty(topics(notes, meeting))
    )
}

fn or_empty(text: String) -> String {
    if text.is_empty() {
        EMPTY.to_string()
    } else {
        text
    }
}

fn topics(notes: &Notes, meeting: &Meeting) -> String {
    notes
        .topics
        .iter()
        .map(|topic| {
            let bullets: Vec<String> = topic
                .bullets
                .iter()
                .map(|b| format!("- {} {}", b.text, cite(&b.turns, meeting)))
                .collect();
            format!("### {}\n{}", topic.title, bullets.join("\n"))
        })
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn render(notes: &Notes, meeting: &Meeting, view: View, extra_notes: &[String]) -> String {
    let actions: Vec<&Action> = notes
        .actions
        .iter()
        .filter(|a| a.kind == Kind::Action)
        .collect();
    let follow: Vec<&Action> = notes
        .actions
        .iter()
        .filter(|a| a.kind == Kind::FollowUp)
        .collect();
    let all: Vec<&Action> = notes.actions.iter().collect();
    let notes_block = || format!("**Notes**\n{}", or_empty(topics(notes, meeting)));
    let body = match view {
        View::Decisions => vec![decisions(notes, meeting, false)],
        View::Actions => vec![actions_table(&actions, meeting)],
        View::Followups => vec![follow_ups(&follow, meeting)],
        View::Mine => vec![format!(
            "**Yours ({})**\n{}",
            meeting.my_name,
            list(&mine_only(&notes.actions, meeting), meeting, false)
        )],
        View::Sources => vec![
            decisions(notes, meeting, true),
            format!(
                "**Action items and follow-ups**\n{}",
                list(&all, meeting, true)
            ),
        ],
        View::Table => vec![table(notes, meeting)],
        View::Notes => vec![notes_block()],
        View::Default | View::Json => vec![
            notes_block(),
            decisions(notes, meeting, false),
            actions_table(&actions, meeting),
            follow_ups(&follow, meeting),
        ],
    };
    std::iter::once(header(meeting))
        .chain(body)
        .chain(std::iter::once(footer(notes, meeting, extra_notes)))
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}

pub fn render_json(envelope: &Value) -> String {
    let body = serde_json::to_string_pretty(envelope).unwrap_or_else(|_| "{}".to_string());
    format!("```json\n{body}\n```")
}

fn count(n: usize) -> String {
    if n == 1 {
        format!("{n} item")
    } else {
        format!("{n} items")
    }
}

pub fn footer(notes: &Notes, meeting: &Meeting, extra_notes: &[String]) -> String {
    let mut lines = vec![if meeting.has_speakers {
        format!(
            "{} turns, {} speakers.",
            meeting.turns.len(),
            meeting.attendees.len()
        )
    } else {
        format!("{} lines, no speaker labels.", meeting.turns.len())
    }];
    if notes.dropped > 0 {
        lines.push(format!(
            "{} left out: no supporting quote.",
            count(notes.dropped)
        ));
    }
    if notes.capped > 0 {
        lines.push(format!(
            "{} more left out to keep the lists short.",
            count(notes.capped)
        ));
    }
    std::iter::once(format!("_({})_", lines.join(" ")))
        .chain(
            notes
                .notes
                .iter()
                .chain(extra_notes)
                .filter(|n| !n.is_empty())
                .cloned(),
        )
        .collect::<Vec<_>>()
        .join("\n")
}

fn quote(text: &str) -> String {
    format!("\n   > \"{}\"", clean_text(text, text))
}

fn dash(text: &str) -> &str {
    if text.is_empty() { "-" } else { text }
}

fn decisions(notes: &Notes, meeting: &Meeting, sources: bool) -> String {
    let lines: Vec<String> = notes
        .decisions
        .iter()
        .enumerate()
        .map(|(n, d)| {
            let (speaker, time) = meeting.when(&d.turn);
            let who: Vec<&str> = [speaker.as_str(), time.as_str()]
                .into_iter()
                .filter(|p| !p.is_empty() && *p != "Unknown")
                .collect();
            let who = if who.is_empty() {
                String::new()
            } else {
                format!(" ({})", who.join(", "))
            };
            let shown = if sources {
                quote(&d.quote)
            } else {
                String::new()
            };
            format!("{}. {}{who}{shown}", n + 1, d.text)
        })
        .collect();
    format!("**Decisions**\n{}", or_empty(lines.join("\n")))
}

fn owner(a: &Action, meeting: &Meeting) -> String {
    let mark = if meeting.mine(&a.owner) == Some(true) {
        " *"
    } else {
        ""
    };
    format!("{}{mark}", a.owner)
}

fn actions_table(actions: &[&Action], meeting: &Meeting) -> String {
    if actions.is_empty() {
        return format!("**Action items**\n{EMPTY}");
    }
    let rows: Vec<String> = actions
        .iter()
        .enumerate()
        .map(|(n, a)| {
            format!(
                "| {} | {} | {} | {} | {} |",
                n + 1,
                owner(a, meeting),
                a.task,
                dash(&a.due),
                dash(&meeting.when(&a.turn).1)
            )
        })
        .collect();
    let mark = if meeting.my_name.is_empty() {
        ""
    } else {
        " (yours marked *)"
    };
    format!(
        "**Action items**{mark}\n| # | Owner | Task | Due | Time |\n|---|---|---|---|---|\n{}",
        rows.join("\n")
    )
}

fn follow_ups(follow: &[&Action], meeting: &Meeting) -> String {
    if follow.is_empty() {
        return format!("**Follow-ups**\n{EMPTY}");
    }
    let rows: Vec<String> = follow
        .iter()
        .enumerate()
        .map(|(n, a)| {
            format!(
                "| {} | {} | {} | {} |",
                n + 1,
                owner(a, meeting),
                a.task,
                dash(&a.due)
            )
        })
        .collect();
    format!(
        "**Follow-ups**\n| # | Owner | Item | Due |\n|---|---|---|---|\n{}",
        rows.join("\n")
    )
}

fn list(actions: &[&Action], meeting: &Meeting, sources: bool) -> String {
    let lines: Vec<String> = actions
        .iter()
        .enumerate()
        .map(|(n, a)| {
            let kind = if a.kind == Kind::FollowUp {
                "follow-up"
            } else {
                "action"
            };
            let owner = if a.owner.is_empty() {
                NOT_STATED
            } else {
                &a.owner
            };
            let shown = if sources {
                quote(&a.quote)
            } else {
                String::new()
            };
            format!(
                "{}. {} ({kind}; owner: {owner}; due: {}) {}{shown}",
                n + 1,
                a.task,
                dash(&a.due),
                cite(std::slice::from_ref(&a.turn), meeting)
            )
        })
        .collect();
    or_empty(lines.join("\n"))
}

fn table(notes: &Notes, meeting: &Meeting) -> String {
    let mut rows: Vec<String> = notes
        .decisions
        .iter()
        .map(|d| {
            format!(
                "| Decision | {} | - | - | {} |",
                d.text,
                dash(&meeting.when(&d.turn).1)
            )
        })
        .collect();
    rows.extend(notes.actions.iter().map(|a| {
        let kind = if a.kind == Kind::FollowUp {
            "Follow-up"
        } else {
            "Action"
        };
        format!(
            "| {kind} | {} | {} | {} | {} |",
            a.task,
            owner(a, meeting),
            dash(&a.due),
            dash(&meeting.when(&a.turn).1)
        )
    }));
    if rows.is_empty() {
        return format!("**Decisions and action items**\n{EMPTY}");
    }
    format!(
        "**Decisions and action items**\n| Type | Item | Owner | Due | Time |\n|---|---|---|---|---|\n{}",
        rows.join("\n")
    )
}
