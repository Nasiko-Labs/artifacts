"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "adhoc-request-deflector"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Checks whether an existing dashboard already answers an ad-hoc data request (\"can you pull weekly "
    "active users by plan?\") and drafts a short reply with the dashboard link. It reads your dashboard index "
    "(a spreadsheet titled like \"Dashboard Index\", or a list you paste: name, url, description), ranks the "
    "dashboards against the request in code, and labels the match exact, partial (what is covered and what is "
    "missing) or none. Named a chat channel (\"check #data-requests since Monday\"), it scans recent requests "
    "there, up to 5 per turn, and drafts a reply for each; a reply is posted in the request's thread only after "
    "you approve that exact text, one card per post. It never answers the data question itself, never claims "
    "a match the index does not support, and links only to dashboards in the index."
)

SKILL_ID = "deflect-requests"
SKILL_NAME = "Deflect data requests"
SKILL_DESCRIPTION = (
    "Matches a data request to an existing dashboard from your dashboard index (exact / partial / none, with "
    "what is covered and missing) and drafts a reply with the link; scans a chat channel for recent requests "
    "and posts each reply in its thread only after you approve it."
)
SKILL_TAGS = ["data requests", "dashboards", "deflection", "analytics", "self-serve", "chat"]
SKILL_EXAMPLES = [
    "can you pull weekly active users by plan?",
    "check #data-requests since Monday",
    "is there a dashboard for churn?",
]
