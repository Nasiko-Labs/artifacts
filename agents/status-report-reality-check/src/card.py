"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "status-report-reality-check"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Checks a status report against a ticket export, both pasted into the chat, and lists where the "
    "reported status and the tickets disagree: 'on track' while the ticket is blocked or overdue, 'done' "
    "while the epic is 27% done, 'green' with nothing touched in weeks. Each disagreement shows the item, "
    "what the report says (quoted), what the tickets say (built in code from the export's own cells: "
    "status, dates, assignee, children done) and a risk level from fixed rules. Matching, dates and rules "
    "run in code; the model only reads the report. Report items with no ticket are listed separately. "
    "Follow-ups: only high risk, stale after 7 days, show matched tickets, why was X flagged, as json. It "
    "reads only what you paste (CSV/TSV/JSON export), never fetches tickets and never judges people."
)

SKILL_ID = "status-reality-check"
SKILL_NAME = "Status report reality check"
SKILL_DESCRIPTION = (
    "Lists where a pasted status report and a pasted ticket export disagree (item, reported, evidence, "
    "risk), with code-built evidence from the export, plus follow-ups on the same comparison."
)
SKILL_TAGS = ["status report", "tickets", "reality check", "engineering manager", "risk", "epics", "tracker export"]
SKILL_EXAMPLES = [
    "Reality-check this status report against this ticket export: Report: <paste> Tickets: <paste the CSV>",
    "Only high risk",
    "Why did you flag Payments migration?",
]
