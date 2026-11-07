"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "bug-to-customer-notifier"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "When a bug in your tracker is Done or Released, confirms the fix actually shipped (a published, non-draft "
    "release named like the fix version on the code host, not just a merge), finds every open support-desk "
    "ticket linked to that bug or clearly matching it, groups them by customer and tier with each customer's "
    "original complaint, and drafts a plain-language reply for each ticket you pick. Every reply is shown to you "
    "and posted only after you approve that exact text, one ticket at a time; it can also set the ticket to "
    "solved if you say so. It drafts nothing when it can't confirm the release. Ask by issue key (\"PROJ-482 is "
    "released, tell the customers\") or ask which bugs shipped this week. Not a release-notes writer and not a "
    "general support-ticket assistant."
)

SKILL_ID = "shipped-fix-customer-replies"
SKILL_NAME = "Tell customers a bug fix shipped"
SKILL_DESCRIPTION = (
    "Checks that a done or released bug's fix is in a published release, finds the open support tickets linked "
    "to it or matching it, grouped by customer and tier, and drafts one reply per ticket, posted only after you "
    "approve the exact text."
)
SKILL_TAGS = ["bug fix", "release", "support tickets", "customers", "notify", "tracker", "support desk"]
SKILL_EXAMPLES = [
    "PROJ-482 is released, tell the customers.",
    "Which bugs shipped this week that customers are waiting on?",
    "Draft replies for the enterprise tickets and mention the workaround is no longer needed.",
]
