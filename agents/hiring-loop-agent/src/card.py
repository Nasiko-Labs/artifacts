"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "hiring-loop-agent"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Prepares a hiring debrief for ONE candidate: it gathers the interviewers' written feedback (pasted "
    "scorecards, or feedback emails and shared scorecard documents it can read for you), lines it up by "
    "competency, flags where interviewers contradict each other (overall recommendation, the same competency, "
    "opposite claims, a pair interview rated apart), lists what nobody covered, and flags vague or biased "
    "feedback (\"culture fit\", remarks about personal characteristics) so the debrief stays evidence-based. "
    "Every quote is checked word for word against the feedback. It never recommends or decides hire or no-hire, "
    "never rates the candidate overall, never averages or counts votes, and never compares or ranks candidates. "
    "Read-only: it never sends, posts or writes anything; the debrief is markdown to copy."
)

SKILL_ID = "hiring-loop-debrief"
SKILL_NAME = "Interview loop debrief"
SKILL_DESCRIPTION = (
    "A debrief draft for one candidate from the interviewers' feedback: where they agree, contradictions to "
    "resolve with both quotes, competencies nobody covered, feedback-quality flags and questions for the "
    "meeting. Never a hire/no-hire recommendation; read-only."
)
SKILL_TAGS = ["hiring", "interview debrief", "scorecards", "contradictions", "feedback quality", "read-only"]
SKILL_EXAMPLES = [
    "Paste the scorecards for Jordan Lee and draft the debrief",
    "Find the interview feedback for Sam Park in my email",
    "Where do the interviewers disagree on Priya?",
]
