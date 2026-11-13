"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "chunker-merger-stateless"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Cuts a long text into numbered, ready-to-paste parts and merges the per-part JSON results back into "
    "one result with the same schema, entirely in code with no model. Split: by speaker turns, headings, "
    "paragraphs or sentences, into N parts or parts of a given size, never mid-sentence, optionally with "
    "an instruction wrapped into every part's prompt; it checks that the parts rejoin to your text "
    "exactly. Merge: paste two or more JSON results from the same agent; lists are concatenated and "
    "exact duplicates removed, confidence is the lowest, needs_human is true if any part needs it, "
    "near-duplicates are only reported. Stateless: it keeps nothing between messages, so follow-ups (show "
    "part 3, split again, merge with this one) work on the text or results still visible in the recent "
    "conversation and a long earlier paste may need re-pasting; a text sent over several messages is split "
    "piece by piece. It never asks back. It never rewrites content, calls no other agent and answers no "
    "other questions."
)

SKILL_ID = "chunk-and-merge"
SKILL_NAME = "Split long text and merge results"
SKILL_DESCRIPTION = (
    "Splits a long text into numbered chunk prompts on structural boundaries, losslessly, and merges "
    "same-schema JSON results from the parts into one, without rewriting any item."
)
SKILL_TAGS = ["split", "chunk", "merge", "long text", "transcript", "json", "map-reduce"]
SKILL_EXAMPLES = [
    "Split this transcript into 4 parts, for each part: extract action items <paste the text>",
    "Merge these results: <json> --- <json>",
    "Show part 3",
]
