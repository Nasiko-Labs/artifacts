//! Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
//! Times, speakers, attendees, `mine`, `confidence` and `needs_human` are computed here, never taken from
//! the model.

use serde_json::{Value, json};

use crate::attendees::Attendee;
use crate::items::{Action, Decision, Kind, NOT_STATED, Notes, Turn};
use crate::owners::is_mine;

pub const HIGH_MAX_DROP: f64 = 0.10;
pub const LOW_MIN_DROP: f64 = 0.30;
pub const LOW_COVERAGE: f64 = 0.70;
pub const EMPTY_LONG_WORDS: usize = 1_000;

#[derive(Debug, Clone, Default)]
pub struct Meeting {
    pub turns: Vec<Turn>,
    pub attendees: Vec<Attendee>,
    pub has_speakers: bool,
    pub title: String,
    pub date: String,
    pub words: usize,
    pub my_name: String,
}

impl Meeting {
    pub fn turn(&self, turn_id: &str) -> Option<&Turn> {
        self.turns.iter().find(|t| t.id == turn_id)
    }

    pub fn names(&self) -> Vec<String> {
        self.attendees.iter().map(|a| a.name.clone()).collect()
    }

    pub fn minutes(&self) -> i64 {
        let times: Vec<i64> = self
            .turns
            .iter()
            .map(|t| t.secs)
            .filter(|s| *s >= 0)
            .collect();
        match (times.iter().max(), times.iter().min()) {
            (Some(max), Some(min)) if times.len() >= 2 => {
                ((max - min) as f64 / 60.0).round() as i64
            }
            _ => 0,
        }
    }

    /// (speaker, time) of a cited turn, ("", "") when it is unknown.
    pub fn when(&self, turn_id: &str) -> (String, String) {
        self.turn(turn_id)
            .map_or((String::new(), String::new()), |t| {
                (t.speaker.clone(), t.time.clone())
            })
    }

    pub fn mine(&self, owner: &str) -> Option<bool> {
        is_mine(owner, &self.my_name, &self.names())
    }
}

pub fn drop_rate(notes: &Notes) -> f64 {
    let total = notes.kept_before_caps + notes.dropped;
    if total == 0 {
        0.0
    } else {
        notes.dropped as f64 / total as f64
    }
}

pub fn confidence(notes: &Notes, meeting: &Meeting) -> &'static str {
    let rate = drop_rate(notes);
    let no_items = notes.decisions.is_empty() && notes.actions.is_empty();
    if rate > LOW_MIN_DROP
        || notes.failed_parts >= 2
        || notes.coverage < LOW_COVERAGE
        || (no_items && meeting.words > EMPTY_LONG_WORDS)
    {
        return "low";
    }
    if meeting.has_speakers
        && meeting.attendees.len() >= 2
        && notes.failed_parts == 0
        && rate <= HIGH_MAX_DROP
        && notes.coverage >= 1.0
    {
        return "high";
    }
    "medium"
}

pub fn needs_human(notes: &Notes, level: &str) -> bool {
    level == "low"
        || notes
            .actions
            .iter()
            .any(|a| a.kind == Kind::Action && a.owner == NOT_STATED)
}

pub fn decision_json(d: &Decision, meeting: &Meeting) -> Value {
    let (speaker, time) = meeting.when(&d.turn);
    json!({"text": d.text, "time": time, "speaker": speaker, "quote": d.quote, "turn": d.turn})
}

pub fn action_json(a: &Action, meeting: &Meeting) -> Value {
    let (_, time) = meeting.when(&a.turn);
    json!({"owner": a.owner, "task": a.task, "due": a.due, "kind": a.kind.as_str(),
           "mine": meeting.mine(&a.owner), "time": time, "quote": a.quote, "turn": a.turn})
}

pub fn summary_text(notes: &Notes) -> String {
    let firsts: Vec<String> = notes
        .topics
        .iter()
        .take(2)
        .filter_map(|t| t.bullets.first())
        .map(|b| {
            if b.text.ends_with(['.', '…', '!', '?']) {
                b.text.clone()
            } else {
                format!("{}.", b.text)
            }
        })
        .collect();
    if !firsts.is_empty() {
        return firsts.join(" ");
    }
    let actions = notes
        .actions
        .iter()
        .filter(|a| a.kind == Kind::Action)
        .count();
    format!(
        "{} decisions and {actions} actions recorded.",
        notes.decisions.len()
    )
}

pub fn build(notes: &Notes, meeting: &Meeting, notes_markdown: &str) -> Value {
    let level = confidence(notes, meeting);
    let items: Vec<Value> = notes
        .actions
        .iter()
        .map(|a| action_json(a, meeting))
        .collect();
    json!({
        "summary": summary_text(notes),
        "items": items,
        "notes": notes_markdown,
        "attendees": meeting.attendees.iter().map(Attendee::to_json).collect::<Vec<_>>(),
        "decisions": notes.decisions.iter().map(|d| decision_json(d, meeting)).collect::<Vec<_>>(),
        "action_items": items,
        "title": meeting.title,
        "date": meeting.date,
        "turns": meeting.turns.len(),
        "left_out": notes.dropped,
        "confidence": level,
        "needs_human": needs_human(notes, level),
    })
}

pub fn mine_only<'a>(actions: &'a [Action], meeting: &Meeting) -> Vec<&'a Action> {
    actions
        .iter()
        .filter(|a| meeting.mine(&a.owner) == Some(true))
        .collect()
}
