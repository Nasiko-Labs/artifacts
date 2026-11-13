//! The meeting's records.
//!
//! Turns, times and speakers come from the parse; topics, decisions and actions are model output that
//! survived the code checks. Speakers and times of items are always looked up from the cited turn.

pub const NOT_STATED: &str = "Not stated";
pub const UNKNOWN: &str = "Unknown";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Turn {
    /// T1..Tn (L1..Ln for the lines of an earlier notes reply).
    pub id: String,
    /// As labelled, or "Unknown" for raw notes.
    pub speaker: String,
    /// As displayed ("14:22", "1:02:03"), "" when the source has none.
    pub time: String,
    pub text: String,
    /// The time in seconds, -1 when unknown.
    pub secs: i64,
}

impl Turn {
    pub fn new(id: &str, speaker: &str, time: &str, text: &str) -> Self {
        Self {
            id: id.to_string(),
            speaker: speaker.to_string(),
            time: time.to_string(),
            text: text.to_string(),
            secs: -1,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Bullet {
    pub text: String,
    pub turns: Vec<String>,
    pub snippet: String,
    pub item_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Topic {
    pub title: String,
    pub bullets: Vec<Bullet>,
    pub item_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Decision {
    pub text: String,
    pub turn: String,
    pub quote: String,
    pub item_id: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Action,
    FollowUp,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Action => "action",
            Kind::FollowUp => "follow_up",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Action {
    pub task: String,
    pub kind: Kind,
    /// A name, or NOT_STATED.
    pub owner: String,
    /// As written, "" when not stated near the quote.
    pub due: String,
    pub turn: String,
    pub quote: String,
    pub item_id: String,
}

/// The checked notes of one meeting.
#[derive(Debug, Clone, PartialEq)]
pub struct Notes {
    pub topics: Vec<Topic>,
    pub decisions: Vec<Decision>,
    pub actions: Vec<Action>,
    pub kept_before_caps: usize,
    /// Items the code could not back with a quote in their turn.
    pub dropped: usize,
    /// Items over the section caps.
    pub capped: usize,
    pub failed_parts: usize,
    pub total_parts: usize,
    /// Share of the transcript the map step covered.
    pub coverage: f64,
    pub notes: Vec<String>,
}

impl Default for Notes {
    fn default() -> Self {
        Self {
            topics: Vec::new(),
            decisions: Vec::new(),
            actions: Vec::new(),
            kept_before_caps: 0,
            dropped: 0,
            capped: 0,
            failed_parts: 0,
            total_parts: 1,
            coverage: 1.0,
            notes: Vec::new(),
        }
    }
}

impl Notes {
    pub fn is_empty(&self) -> bool {
        self.topics.is_empty() && self.decisions.is_empty() && self.actions.is_empty()
    }
}
