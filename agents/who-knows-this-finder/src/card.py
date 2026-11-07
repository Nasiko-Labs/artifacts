"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "who-knows-this-finder"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Given a topic (\"who understands the billing retry logic?\"), names at most two people with the "
    "strongest evidence of knowing it, read from your own connected code host, chat and docs: commits to the "
    "matching files, pull requests they opened or reviewed, chat questions on the topic they answered (and "
    "were thanked for), documents they wrote or last edited. Every person comes with linked evidence; the "
    "score is computed in code from that evidence only (kind, recency, breadth across sources), never from "
    "seniority or performance. When the evidence is thin it says \"no clear owner\" instead of guessing. "
    "Read-only: it never messages anyone. Commit authorship stands in for line-level blame. Not a people "
    "directory, an org chart or a performance tool."
)

SKILL_ID = "who-knows"
SKILL_NAME = "Who knows this"
SKILL_DESCRIPTION = (
    "The two people with the best evidence of knowing a topic, from commits, pull request reviews, answered "
    "chat questions and documents, each with linked evidence; \"no clear owner\" when the evidence is thin."
)
SKILL_TAGS = ["expertise", "who knows", "owner", "subject matter expert", "code owner", "ask someone"]
SKILL_EXAMPLES = [
    "Who knows our billing retry logic?",
    "Who should I ask about the auth middleware?",
    "Who understands the deploy pipeline?",
]
