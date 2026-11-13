//! Card text, shared by the served card (`server.rs`) and `AgentCard.json` (checked by a test).
//!
//! The description carries the distinction from other agents: workflow generation sees only the
//! name and description.

use a2a::{
    AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill,
    TRANSPORT_PROTOCOL_JSONRPC,
};

pub const AGENT_NAME: &str = "summarizer-rust";
pub const AGENT_VERSION: &str = "1.0.0";
pub const AGENT_DESCRIPTION: &str = concat!(
    "Summarizes long text you paste into the chat (a meeting transcript, an email thread, a chat ",
    "export, a document, another agent's output) into a short TL;DR, the decisions it records and the ",
    "action items with owners and due dates. Every decision and action item is backed by a verbatim ",
    "quote from your text, checked in code; anything it can't find in your text is left out, and it ",
    "says how many. Follow-ups work on what is still in the recent conversation: only the action items, ",
    "only decisions, shorter, show sources, as a table, who owns what, or a question about the text. It ",
    "keeps nothing between messages, so a very long text from earlier may need to be pasted again. Up ",
    "to about 20k words per message (anything longer is summarized up to the limit and it says what was ",
    "left out). It reads only what you give it: no mail, files or web, and no general questions. This is ",
    "the Rust build: the same summarizer as a small native binary."
);

pub const SKILL_ID: &str = "summarize-text";
pub const SKILL_NAME: &str = "Summarize text";
pub const SKILL_DESCRIPTION: &str = concat!(
    "A TL;DR, the decisions and the action items (owner, due) of the text you paste, each backed by a ",
    "quote from that text, plus follow-ups on the same summary while it is still in the recent conversation."
);
pub const SKILL_TAGS: &[&str] = &[
    "summary",
    "tl;dr",
    "meeting notes",
    "transcript",
    "decisions",
    "action items",
    "owners",
];
pub const SKILL_EXAMPLES: &[&str] = &[
    "Summarize this call: <paste the transcript>",
    "TL;DR of this thread, focus on pricing: <paste the thread>",
    "Only the action items, with sources.",
];
pub const INPUT_MODES: &[&str] = &[
    "text/plain",
    "text/markdown",
    "text/csv",
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
