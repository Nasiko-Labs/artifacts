"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "prioritizer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Ranks a task list you paste into the chat (free text, bullets, or another agent's action items), with "
    "optional goals, into an ordered table with a score and a reason for each task, quick wins, and a short list "
    "of tasks to drop or delegate. The model only rates impact and effort on 1-5 rubrics; urgency (from due "
    "phrases like 'by Friday' or 'Nov 12'), scores, ranks and the drop/delegate rule are computed in code, and "
    "an estimate you write ('2h', '~3 days') beats the model's effort. Follow-ups re-rank with no new model "
    "call: urgency first, impact first, quick wins first, move 3 up, top 5, show the numbers, as of a date, as "
    "json. It never invents deadlines, owners or estimates, and changes nothing anywhere. Not for scheduling, "
    "creating tasks or advice on how to do them."
)

SKILL_ID = "prioritize-tasks"
SKILL_NAME = "Prioritize tasks"
SKILL_DESCRIPTION = (
    "A ranked list of the tasks you paste, scored in code from impact, urgency and effort, with a reason per "
    "task, quick wins and what to drop or delegate; re-weight or re-date without new calls."
)
SKILL_TAGS = ["prioritization", "tasks", "to-do", "ranking", "triage", "focus", "delegate"]
SKILL_EXAMPLES = [
    ("Prioritise these: fix the checkout bug (1h); write the Friday report; renew the vendor by Nov 12, "
     "goals: ship v2, today is 2026-11-05"),
    "Rank my tasks, urgency first",
    "What should I drop?",
]
