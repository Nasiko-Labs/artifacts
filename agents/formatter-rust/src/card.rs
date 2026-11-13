//! Card text, shared by the served card (`server.rs`) and `AgentCard.json` (checked by a test).
//!
//! The description carries the distinction from other agents: workflow generation sees only the name
//! and description.

pub const AGENT_NAME: &str = "formatter-rust";
pub const AGENT_VERSION: &str = "1.0.0";
pub const AGENT_DESCRIPTION: &str = "Formats structured output (a JSON envelope from another agent, or any JSON) as Markdown, Slack \
Block Kit blocks or email HTML, in code: same JSON in, same text out. This is the Rust build. It never adds, rewrites or \
summarises content; every value in your JSON appears in the result, escaped for the target (Slack \
data can never ping a channel; email HTML has no scripts, images or external styles). Knows the \
layouts of the catalog agents' outputs (summaries, action items, promises, rankings, pre-mortems, \
drafts, memos) and has a generic layout for any other JSON; also converts pasted Markdown to \
Slack or email. Follow-ups re-render the same input: as slack, as email, as markdown, compact, no \
footer, the plain-text version, as json. Stateless: it keeps nothing between messages, so a \
follow-up works on the JSON or Markdown still visible in the recent conversation; a long earlier \
paste may have dropped out of it and need re-pasting. Broken JSON gets a plain 'fix it and paste \
again', never a question. It sends and posts nothing, and answers no other questions.";

pub const SKILL_ID: &str = "format-output";
pub const SKILL_NAME: &str = "Format structured output";
pub const SKILL_DESCRIPTION: &str = "Renders a JSON envelope or any JSON as Markdown, Slack blocks or email HTML without changing \
its content, plus Markdown to Slack or email conversion.";
pub const SKILL_TAGS: [&str; 8] = [
    "format",
    "markdown",
    "slack",
    "block kit",
    "email",
    "html",
    "json",
    "render",
];
pub const SKILL_EXAMPLES: [&str; 3] = [
    "Format this as Slack: <json>",
    "Format as email: <json>",
    "As markdown, compact: <json>",
];
pub const INPUT_MODES: [&str; 3] = ["text/plain", "text/markdown", "application/json"];
pub const OUTPUT_MODES: [&str; 1] = ["text/plain"];
