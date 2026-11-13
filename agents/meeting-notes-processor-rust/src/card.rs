//! Card text, shared by the served card (`server.rs`) and `AgentCard.json` (checked by a test).
//!
//! The description carries the distinction from other agents: workflow generation sees only the
//! name and description.

use a2a::{
    AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill,
    TRANSPORT_PROTOCOL_JSONRPC,
};

pub const AGENT_NAME: &str = "meeting-notes-processor-rust";
pub const AGENT_VERSION: &str = "1.0.0";
pub const AGENT_DESCRIPTION: &str = concat!(
    "Turns a meeting transcript or raw meeting notes you paste into structured notes: the attendees with ",
    "their share of the talking, topic notes, the decisions, and the action items and follow-ups with owners ",
    "and due dates. It reads speaker and timestamp transcripts (Zoom, Teams, Otter style, VTT, SRT) and plain ",
    "bullet notes. Every decision and action cites the turn and time it came from and a verbatim quote, ",
    "checked in code; it never invents an owner or a date, and resolves hand-offs ('Mei, can you take that?' ",
    "'On it') to the person who accepted. Follow-ups: only decisions, only action items, only mine (after ",
    "'I'm Raj'), only follow-ups, show sources, as a table, as json, or a question about the meeting. ",
    "Follow-ups work on what is still in the recent conversation; it keeps nothing between messages, so a ",
    "long transcript from earlier may need to be pasted again. Up to about 20k words (anything longer is ",
    "processed up to the limit and it says what was left out). It sends nothing and reads only what you ",
    "give it. This is the Rust build: the same meeting notes processor as a small native binary."
);

pub const SKILL_ID: &str = "process-meeting-notes";
pub const SKILL_NAME: &str = "Process meeting notes";
pub const SKILL_DESCRIPTION: &str = concat!(
    "Attendees, topic notes, decisions, action items and follow-ups (owner, due, time) from a pasted meeting ",
    "transcript or notes, each backed by a quote from its turn, plus follow-ups on the same meeting while it is ",
    "still in the recent conversation."
);
pub const SKILL_TAGS: &[&str] = &[
    "meeting notes",
    "transcript",
    "minutes",
    "decisions",
    "action items",
    "follow-ups",
    "owners",
    "attendees",
];
pub const SKILL_EXAMPLES: &[&str] = &[
    "Process this transcript: <paste>",
    "Notes from the sync, I'm Raj: <paste>",
    "What did we decide about pricing?",
];
pub const INPUT_MODES: &[&str] = &[
    "text/plain",
    "text/markdown",
    "application/json",
    "text/vtt",
    "application/x-subrip",
];
pub const OUTPUT_MODES: &[&str] = &["text/plain"];

fn strings(items: &[&str]) -> Vec<String> {
    items.iter().map(|s| s.to_string()).collect()
}

/// The card served at `/.well-known/agent-card.json`.
pub fn agent_card(port: u16) -> AgentCard {
    AgentCard {
        name: AGENT_NAME.into(),
        description: AGENT_DESCRIPTION.into(),
        version: AGENT_VERSION.into(),
        provider: Some(AgentProvider {
            organization: "Nasiko".into(),
            url: "https://nasiko.io".into(),
        }),
        capabilities: AgentCapabilities {
            streaming: Some(true),
            push_notifications: Some(false),
            extensions: None,
            extended_agent_card: None,
        },
        skills: vec![AgentSkill {
            id: SKILL_ID.into(),
            name: SKILL_NAME.into(),
            description: SKILL_DESCRIPTION.into(),
            tags: strings(SKILL_TAGS),
            examples: Some(strings(SKILL_EXAMPLES)),
            input_modes: Some(strings(INPUT_MODES)),
            output_modes: Some(strings(OUTPUT_MODES)),
            security_requirements: None,
        }],
        default_input_modes: strings(INPUT_MODES),
        default_output_modes: strings(OUTPUT_MODES),
        supported_interfaces: vec![AgentInterface::new(
            format!("http://0.0.0.0:{port}/"),
            TRANSPORT_PROTOCOL_JSONRPC,
        )],
        security_schemes: None,
        security_requirements: None,
        documentation_url: None,
        icon_url: None,
        signatures: None,
    }
}
