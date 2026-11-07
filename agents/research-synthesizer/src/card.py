"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "research-synthesizer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Synthesizes a pile of sources you paste into the chat (interview notes, articles, reports, forum "
    "threads, call notes) against your research question into an evidence-preserving brief: findings "
    "with the verbatim quotes and source ids behind them, where sources agree or disagree, a confidence "
    "per finding computed in code, what was read, and what is still unknown. It uses only the pasted text: "
    "no browsing, no outside knowledge, and links are labels it never opens. Follow-ups work on the stored "
    "brief: only the conflicts, show quotes, high-confidence findings, list sources, what the sources say "
    "about a topic, or add another source. No general questions."
)

SKILL_ID = "synthesize-sources"
SKILL_NAME = "Synthesize research sources"
SKILL_DESCRIPTION = (
    "An evidence-preserving brief over the sources you paste: findings with quotes and [S#] source ids, "
    "agreements and conflicts, code-computed confidence, scope and unknowns, plus follow-ups on the same "
    "brief."
)
SKILL_TAGS = ["research", "synthesis", "sources", "interviews", "evidence", "conflicts", "unknowns"]
SKILL_EXAMPLES = [
    "Synthesize these 5 interviews: what do customers say about onboarding? <paste the sources>",
    "Only the conflicts",
    "What do the sources say about pricing?",
]
