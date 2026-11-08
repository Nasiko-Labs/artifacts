"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name and
description."""

AGENT_NAME = "app-store-review-miner"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Mines app store and Google Play reviews you paste (blocks, CSV/TSV or JSON; up to 400 reviews a turn): "
    "groups them by app version, finds the complaint themes with review counts, shares and verbatim quotes, "
    "flags versions where ratings dropped (low-rating share up 15 points or average down 0.5 stars against the "
    "previous version, with at least 10 rated reviews each), lists complaints new in a version, and compares "
    "complaint shares with competitor apps whose reviews you also paste. Every count, average and percentage "
    "is computed in code. If a review tool is connected it can fetch an app's recent reviews instead. It never "
    "rates an app from memory, fetches store pages, invents reviews or replies to them. Follow-ups: themes, "
    "versions, compare, as json. Not a general product or survey analyst."
)

SKILL_ID = "app-review-themes"
SKILL_NAME = "App review themes"
SKILL_DESCRIPTION = (
    "Complaint themes with counts and verbatim quotes from pasted app store reviews, per-version ratings with "
    "regression flags, complaints new in a version, and complaint shares compared with competitor apps."
)
SKILL_TAGS = ["app store", "google play", "app reviews", "ratings", "release regression", "voice of customer",
              "competitors", "product management"]
SKILL_EXAMPLES = [
    "Here are our last 200 reviews, what is wrong with 4.2.1?",
    "Compare our complaints with Rival App's",
    "Which version got worse?",
]
