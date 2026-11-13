//! Pure: who spoke, in order of first appearance, with turn counts and speaking share (integer %, floored,
//! so the shares never sum past 100). Names mentioned but not speaking are not attendees.

use serde_json::{Value, json};

use crate::items::{Turn, UNKNOWN};

const SYSTEM_LABELS: &[&str] = &[
    "transcript",
    "recording",
    "system",
    "unknown",
    "speaker",
    "bot",
    "notetaker",
];
pub const NOT_IDENTIFIED: &str = "Not identified (notes have no speaker labels)";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Attendee {
    pub name: String,
    pub turns: usize,
    /// % of the attendees' turns.
    pub share: usize,
}

impl Attendee {
    pub fn to_json(&self) -> Value {
        json!({"name": self.name, "turns": self.turns, "share": self.share})
    }
}

pub fn is_system(name: &str) -> bool {
    SYSTEM_LABELS.contains(&name.trim().to_lowercase().as_str())
}

pub fn attendees(turns: &[Turn]) -> Vec<Attendee> {
    let mut counts: Vec<(String, usize)> = Vec::new();
    for turn in turns {
        if turn.speaker == UNKNOWN || is_system(&turn.speaker) {
            continue;
        }
        match counts.iter_mut().find(|(name, _)| *name == turn.speaker) {
            Some((_, n)) => *n += 1,
            None => counts.push((turn.speaker.clone(), 1)),
        }
    }
    let total: usize = counts.iter().map(|(_, n)| n).sum();
    if total == 0 {
        return Vec::new();
    }
    counts
        .into_iter()
        .map(|(name, n)| Attendee {
            name,
            turns: n,
            share: n * 100 / total,
        })
        .collect()
}

pub fn speaker_names(turns: &[Turn]) -> Vec<String> {
    attendees(turns).into_iter().map(|a| a.name).collect()
}
