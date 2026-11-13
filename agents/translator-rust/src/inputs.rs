//! Pure: what the user gave this turn, read from the A2A message parts.
//!
//! - The user's own words are the FIRST text part. Later text parts are org policy or agent knowledge:
//!   they reach the model as instructions (a glossary, "always formal for customers"), never as text to
//!   translate.
//! - In a session with prior turns the text arrives as `"{history}\n\nCurrent message: {text}"`. The text
//!   after the last marker is this turn's message; the history before it is parsed into turns
//!   (`history.rs`) and is this agent's only memory: it keeps nothing between messages.
//! - A data part holding a JSON object is an envelope from another agent; message metadata
//!   `{"output": "json"}` asks for the envelope as the reply text (a workflow step).

use a2a::{Message, PartContent};
use serde_json::{Map, Value};

use crate::history::{self, Turn};

pub const FILE_NOTE: &str =
    "I can't read attached files yet; paste the text into the message instead.";

#[derive(Debug, Clone, Default)]
pub struct Incoming {
    pub text: String,
    pub context: Vec<String>,
    pub data: Option<Map<String, Value>>,
    pub as_json: bool,
    pub notes: Vec<String>,
    pub history: Vec<Turn>,
}

impl Incoming {
    pub fn text(text: &str) -> Self {
        Self {
            text: text.to_string(),
            ..Self::default()
        }
    }
}

pub fn read_message(message: Option<&Message>) -> Incoming {
    let Some(message) = message else {
        return Incoming::default();
    };
    let mut texts: Vec<&str> = Vec::new();
    let mut data: Option<Map<String, Value>> = None;
    let mut notes: Vec<String> = Vec::new();
    for part in &message.parts {
        match &part.content {
            PartContent::Text(text) => texts.push(text),
            PartContent::Data(value) => {
                if data.is_none() {
                    data = value.as_object().cloned();
                }
            }
            PartContent::Raw(_) | PartContent::Url(_) => {
                if !notes.iter().any(|n| n == FILE_NOTE) {
                    notes.push(FILE_NOTE.to_string());
                }
            }
        }
    }
    let as_json = message
        .metadata
        .as_ref()
        .and_then(|m| m.get("output"))
        .and_then(Value::as_str)
        .is_some_and(|v| v.to_lowercase() == "json");
    let (block, user) = texts.first().map_or(("", ""), |t| history::split(t));
    Incoming {
        text: user.to_string(),
        context: texts
            .iter()
            .skip(1)
            .filter(|t| !t.trim().is_empty())
            .map(|t| t.to_string())
            .collect(),
        data,
        as_json,
        notes,
        history: history::parse(block),
    }
}
