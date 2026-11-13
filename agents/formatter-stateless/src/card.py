"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "formatter-stateless"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Formats structured output (a JSON envelope from another agent, or any JSON) as Markdown, Slack "
    "Block Kit blocks or email HTML, in code: same JSON in, same text out. It never adds, rewrites or "
    "summarises content; every value in your JSON appears in the result, escaped for the target (Slack "
    "data can never ping a channel; email HTML has no scripts, images or external styles). Knows the "
    "layouts of the catalog agents' outputs (summaries, action items, promises, rankings, pre-mortems, "
    "drafts, memos) and has a generic layout for any other JSON; also converts pasted Markdown to "
    "Slack or email. Follow-ups re-render the same input: as slack, as email, as markdown, compact, no "
    "footer, the plain-text version, as json. Stateless: it keeps nothing between messages, so a "
    "follow-up works on the JSON or Markdown still visible in the recent conversation; a long earlier "
    "paste may have dropped out of it and need re-pasting. Broken JSON gets a plain 'fix it and paste "
    "again', never a question. It sends and posts nothing, and answers no other questions."
)

SKILL_ID = "format-output"
SKILL_NAME = "Format structured output"
SKILL_DESCRIPTION = (
    "Renders a JSON envelope or any JSON as Markdown, Slack blocks or email HTML without changing "
    "its content, plus Markdown to Slack or email conversion."
)
SKILL_TAGS = ["format", "markdown", "slack", "block kit", "email", "html", "json", "render"]
SKILL_EXAMPLES = [
    "Format this as Slack: <json>",
    "Format as email: <json>",
    "As markdown, compact: <json>",
]
