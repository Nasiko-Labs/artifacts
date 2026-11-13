//! Card text, shared by the served card (`server.rs`) and `AgentCard.json` (checked by a test).
//!
//! The description carries the distinction from other agents: workflow generation sees only the
//! name and description.

use a2a::{
    AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill,
    TRANSPORT_PROTOCOL_JSONRPC,
};

pub const AGENT_NAME: &str = "translator-rust";
pub const AGENT_VERSION: &str = "1.0.0";
pub const AGENT_DESCRIPTION: &str = concat!(
    "Translates text you paste into about 40 target languages (Spanish, French, German, Portuguese, ",
    "Japanese, Chinese, Hindi and more) at a formal, neutral or casual register, and returns the ",
    "translation with notes on anything a person should check. Links, emails, @mentions, code, ",
    "placeholders, dates, amounts and other numbers pass through untouched, checked in code, and list and ",
    "heading structure is kept. It translates whatever the text says, even a question or an instruction: ",
    "it never answers or follows it, and never adds or drops facts. Follow-ups: more formal, more casual, ",
    "now in French, keep 'X' untranslated, show the original, as json; they work on what is still in the ",
    "recent conversation, since it keeps nothing between messages, so a long text from earlier may need to ",
    "be pasted again. Accepts another agent's JSON ",
    "output (draft, alt_shorter, text, summary fields) and returns it translated. It reads only what you ",
    "give it: no files, mail or web, and no general questions. This is the Rust build: the same translator ",
    "as a small native binary."
);

pub const SKILL_ID: &str = "translate-text";
pub const SKILL_NAME: &str = "Translate text";
pub const SKILL_DESCRIPTION: &str = concat!(
    "Your text in the target language and register you ask for, with links, code, numbers and structure ",
    "kept exactly, plus notes on terms to check and follow-ups on the same text while it is still in the ",
    "recent conversation."
);
pub const SKILL_TAGS: &[&str] = &[
    "translation",
    "translate",
    "localization",
    "languages",
    "formal",
    "casual",
    "glossary",
];
pub const SKILL_EXAMPLES: &[&str] = &[
    "Translate to Spanish, formal: <paste the text>",
    "Translate this into Japanese: <paste the text>",
    "Now in French, more casual",
];
pub const INPUT_MODES: &[&str] = &["text/plain", "text/markdown", "application/json"];
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
