//! Card text, shared by the served card (`server.rs`) and `AgentCard.json` (checked by a test).
//!
//! The description carries the distinction from other agents: workflow generation sees only the
//! name and description.

use a2a::{
    AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill,
    TRANSPORT_PROTOCOL_JSONRPC,
};

pub const AGENT_NAME: &str = "task-breakdown-rust";
pub const AGENT_VERSION: &str = "1.0.0";
pub const AGENT_DESCRIPTION: &str = concat!(
    "Turns a goal, spec or messy brief you type or paste into a structured work plan: 3-6 phases named by ",
    "outcome, small tasks with ids, relative sizes (S/M/L/XL), dependencies, 1-3 acceptance criteria and owner ",
    "placeholders. Code computes the order of work, the parallel waves, the critical path in relative units, ",
    "per-phase totals and lanes for a team of N. Also emits a downloadable task_plan.md. Revise it in the same ",
    "chat (split T4, merge T2 and T3, add a security review phase, we are 2 people, make it a 2-week plan for ",
    "one engineer: code fits the plan to the timebox and lists what it deferred) and ask about it (critical ",
    "path?, what depends on T5?) without re-planning. Stateless: it keeps nothing between messages, so ",
    "follow-ups work on the plan still visible in the recent conversation; if a long plan has dropped out of ",
    "it, paste the plan (or its task_plan.md) again. It never asks back: a request with no goal gets a one-line ",
    "ask. It never invents owners, dates or tools and creates nothing in a tracker: the plan is a draft. Not ",
    "for writing the deliverable itself or general questions. This is the Rust build: the same task breakdown ",
    "as a small native binary."
);

pub const SKILL_ID: &str = "task-breakdown-rust";
pub const SKILL_NAME: &str = "Break a goal into a work plan";
pub const SKILL_DESCRIPTION: &str = concat!(
    "Phases, sized tasks with dependencies and acceptance criteria, parallel waves, the critical path and team ",
    "lanes computed in code, plus a task_plan.md file; revisable in the same chat while the plan is still in ",
    "the recent conversation (or pasted back)."
);
pub const SKILL_TAGS: &[&str] = &[
    "planning",
    "task breakdown",
    "work plan",
    "critical path",
    "dependencies",
    "PRD",
    "project",
];
pub const SKILL_EXAMPLES: &[&str] = &[
    "Break down building a customer feedback dashboard into tasks",
    "Here's our PRD: <paste>. Make a phased plan",
    "Plan a 3-week migration from X to Y with 2 engineers",
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
