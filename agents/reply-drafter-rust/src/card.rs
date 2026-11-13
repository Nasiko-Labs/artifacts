//! Card text, shared by the served card (`server.rs`) and `AgentCard.json` (checked by a test).
//!
//! The description carries the distinction from the drafter: workflow generation sees only the name and
//! description.

use a2a::{
    AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill,
    TRANSPORT_PROTOCOL_JSONRPC,
};

pub const AGENT_NAME: &str = "reply-drafter-rust";
pub const AGENT_VERSION: &str = "1.0.0";
pub const AGENT_DESCRIPTION: &str = concat!(
    "Drafts your reply to a message you received, in your own voice. Paste the incoming email, chat or text ",
    "message, say what you want to say back (\"say yes but not before Thursday\", \"decline politely\") and ",
    "paste 1-3 of your past replies: it measures your style from them (length, greeting, sign-off, ",
    "contractions, exclamation marks, emoji) and checks the reply against those numbers. You get the reply, ",
    "a shorter alternative and notes on how the tone was matched. It never invents a fact: anything specific ",
    "you didn't give it becomes a placeholder like [date?], and it never accepts, declines or promises more ",
    "than you said. Revisions (\"shorter\", \"say no instead\") work on what is still in the recent ",
    "conversation; it keeps nothing between messages, so a long message or past replies from earlier may need ",
    "to be pasted again. It only drafts and sends nothing. For a new message with nothing to reply to, use the ",
    "drafter. Not for answering questions, essays, code or translation. This is the Rust build: the same reply ",
    "drafter as a small native binary."
);

pub const SKILL_ID: &str = "draft-reply";
pub const SKILL_NAME: &str = "Draft a reply in your tone";
pub const SKILL_DESCRIPTION: &str = concat!(
    "A reply to a pasted email, chat or text message, written in the style measured from your past replies, ",
    "with a shorter alternative and tone notes; missing specifics become placeholders. Revise it on request ",
    "while it is still in the recent conversation. ",
    "Draft only: nothing is sent."
);
pub const SKILL_TAGS: &[&str] = &[
    "reply",
    "drafting",
    "email",
    "chat message",
    "tone match",
    "writing style",
];
pub const SKILL_EXAMPLES: &[&str] = &[
    "Reply to this email: <paste> My past replies: <1-3 examples>. Say yes but not before Thursday.",
    "Reply to this Slack message: <paste>, decline politely",
    "Make it shorter",
];
pub const INPUT_MODES: &[&str] = &["text/plain"];
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
