"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "commitment-audit"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Audits a team's committed work for a quarter. It takes the quarter's commitments (tracker epics labelled "
    "\"committed\" by default, or a plan document or OKR sheet you link), compares the status someone REPORTED "
    "for each with what the tracker (status, child issues, blockers, due date, last update) and chat discussion "
    "actually show, and lists every commitment with reported status, evidence-based status, confidence and the "
    "specific risk, each backed by a link; disagreements come first. It then drafts a rollup and saves it as a "
    "new document or posts it to one chat channel only after you approve that exact text. Statuses, dates and "
    "progress are computed in code, never guessed. Not personal promises from your own messages, not a chaser "
    "of other people, and it never edits the plan or the tickets."
)

SKILL_ID = "commitment-audit"
SKILL_NAME = "Commitment audit"
SKILL_DESCRIPTION = (
    "A quarter's commitments checked against tracker and chat evidence: reported vs evidence status, "
    "confidence, specific risk and links, disagreements first. Then a rollup, saved as a document or posted "
    "to a channel only after you approve the exact text."
)
SKILL_TAGS = ["commitments", "quarterly planning", "OKR", "status audit", "rollup", "risk", "tracker", "chat"]
SKILL_EXAMPLES = [
    "Audit our Q4 commitments.",
    "Audit last quarter's commitments from the plan at https://example.com/plan",
    "Write the rollup as a document.",
]
