"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "team-pulse"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Prepares a manager's 1:1s: for each person on the team (the people active in the repository or project "
    "you name over the last 2 weeks, up to 10, or the people you name), it reads pull requests opened and "
    "reviewed, review turnaround, commits, in-progress tickets and how long they have sat, and chat message "
    "counts and timing (never message text). It flags patterns, not judgments: review load concentrated on "
    "one person, a ticket 7+ days in its status with no comment, a much quieter week than that person's own "
    "usual, more commits outside their working hours than usual. Each person is compared only with their own "
    "previous 4 weeks, every signal links its evidence, and it shows a team view of review load and blocked "
    "work. Read-only: it never posts, comments, messages anyone or changes anything. It does not rank, rate "
    "or compare people, and refuses rather than guessing when the tools are not connected."
)

SKILL_ID = "team-pulse"
SKILL_NAME = "Team pulse for 1:1s"
SKILL_DESCRIPTION = (
    "Per-person signal cards for the last 2 weeks against each person's own previous 4 weeks (code host, "
    "tracker, chat counts), with evidence links, plus a team view of review load and blocked work. Read-only; "
    "no ranking or judgments."
)
SKILL_TAGS = ["1:1 prep", "team", "manager", "pull requests", "code review", "tickets", "blocked work", "read-only"]
SKILL_EXAMPLES = [
    "Team pulse for the payments-api repo",
    "Prep my 1:1 with Priya",
    "What's blocked on the team?",
]
