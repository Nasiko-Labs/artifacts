"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "board-deck-fact-checker"
AGENT_VERSION = "1.0.3"
AGENT_DESCRIPTION = (
    "Fact-checks a board deck against its source spreadsheets: it reads the deck (a file link or name, or "
    "pasted slide text), extracts every numeric claim (revenue, ARR, burn, headcount, customers, percentages, "
    "growth rates, ratios), finds the cell each claim should come from and compares the numbers in code with "
    "explicit rules: rounding to half the last written digit, truncation labelled, strict or loose on request, "
    "growth and ratios recomputed from the cells, and the period must match. It lists every mismatch with "
    "slide, claim, deck value, source value and cell reference, the claims it could not verify, and the same "
    "figure stated differently on two slides. Pasted source rows work without any connector. Read-only: it "
    "never edits the deck or the sheets and does not judge whether a number is good."
)

SKILL_ID = "board-deck-fact-check"
SKILL_NAME = "Board deck fact check"
SKILL_DESCRIPTION = (
    "Numeric claims in a board deck checked against the source spreadsheet cells (or pasted source rows) in "
    "code: mismatches with cell references and materiality, unverifiable claims, period mismatches and "
    "in-deck inconsistencies. Read-only."
)
SKILL_TAGS = ["board deck", "fact check", "finance", "metrics", "spreadsheet", "leadership", "read-only"]
SKILL_EXAMPLES = [
    "Check the Q3 board deck against the Finance Model sheet",
    "Fact-check this deck text against these numbers",
    "Only check slides 4 to 9, strict rounding",
]
