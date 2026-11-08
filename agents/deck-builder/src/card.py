"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "deck-builder"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns your notes, an outline or pasted data into a real, editable PowerPoint deck (3-20 slides): "
    "action-title slides, short bullets, native editable charts and tables built from the numbers you "
    "pasted, and speaker notes. It shows the slide outline in chat, edits the deck on request (\"make "
    "slide 3 a chart of this table\"), gives a script that rebuilds the file, and uploads the .pptx to "
    "your cloud drive only after you approve the exact file. It never invents numbers, names or quotes "
    "(missing ones become visible placeholders) and never adds images or logos. Not research, long "
    "documents, Word/Excel files or critiques of someone else's deck."
)

SKILL_ID = "build-deck"
SKILL_NAME = "Build a slide deck"
SKILL_DESCRIPTION = (
    "A PowerPoint deck from your notes, outline or pasted data: action titles, short bullets, native "
    "charts and tables from your numbers, speaker notes, placeholders instead of invented facts; edits on "
    "request; uploaded to your drive only after you approve the file."
)
SKILL_TAGS = ["slides", "deck", "powerpoint", "pptx", "presentation", "charts", "board update"]
SKILL_EXAMPLES = [
    "Make an 8-slide board update from these notes: <paste>",
    "Turn this outline into a customer pitch deck: <paste>",
    "Build a deck with a chart of this table: <paste CSV>, then upload it to my Drive",
]
