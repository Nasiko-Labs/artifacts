//! Card text, shared by the served card (`main.rs`) and `AgentCard.json` (checked by a test).
//!
//! The description carries the distinction from other agents: workflow generation sees only the
//! name and description.

use a2a::{
    AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill,
    TRANSPORT_PROTOCOL_JSONRPC,
};

pub const AGENT_NAME: &str = "stack-trace-explainer-rust";
pub const AGENT_VERSION: &str = "1.0.0";
pub const AGENT_DESCRIPTION: &str = concat!(
    "Explains a pasted error message or stack trace (Python, JavaScript/Node, Java/Kotlin, Go, Rust, ",
    "Ruby, C#): the probable cause, the exact line to look at, a fix and how to confirm it. The trace is ",
    "parsed in code, so the location is the innermost frame of your own code (library frames are skipped) ",
    "and chained exceptions are explained root cause first. Paste the code around that line (e.g. under ",
    "'# file: app/orders.py') for a high-confidence answer; without it, it says exactly which lines to ",
    "paste. Every quote, path and line number is checked against what you pasted. It keeps nothing ",
    "between messages: follow-up questions and code pasted later work on the error still in the recent ",
    "conversation, so a very long earlier trace may need pasting again. It never runs code, reads no ",
    "repository or logs, and sends nothing. Not for general programming questions, writing new code or ",
    "code review. This is the Rust build: the same explainer as a small native binary."
);

pub const SKILL_ID: &str = "explain-stack-trace";
pub const SKILL_NAME: &str = "Explain a stack trace";
pub const SKILL_DESCRIPTION: &str = concat!(
    "The probable cause, the line to look at, a fix and how to verify it, for a pasted error or stack trace ",
    "plus optional code snippets. Follow-ups: paste the missing lines, ask why, or ask for the result as json."
);
pub const SKILL_TAGS: &[&str] = &[
    "stack trace",
    "error",
    "exception",
    "debugging",
    "traceback",
    "panic",
    "crash",
];
pub const SKILL_EXAMPLES: &[&str] = &[
    "Explain this: <paste a Python traceback>",
    "Why does this crash? <Java stack trace> + the method code under '// file: OrderRepository.java'",
    "What does `ECONNREFUSED 127.0.0.1:5432` mean?",
];
pub const INPUT_MODES: &[&str] = &[
    "text/plain",
    "text/markdown",
    "application/json",
    "text/x-diff",
    "text/x-patch",
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
