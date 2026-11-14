"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "summarizer-stateless"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Summarizes long text you paste into the chat (a meeting transcript, an email thread, a chat "
    "export, a document, another agent's output) into a short TL;DR, the decisions it records and the "
    "action items with owners and due dates. Every decision and action item is backed by a verbatim "
    "quote from your text, checked in code; anything it can't find in your text is left out, and it "
    "says how many. Follow-ups work on what is still in the recent conversation: only the action items, "
    "only decisions, shorter, show sources, as a table, who owns what, or a question about the text. It "
    "keeps nothing between messages, so a very long text from earlier may need to be pasted again. Up "
    "to about 20k words per message (anything longer is summarized up to the limit and it says what was "
    "left out). It reads only what you give it: no mail, files or web, and no general questions."
)

SKILL_ID = "summarize-text"
SKILL_NAME = "Summarize text"
SKILL_DESCRIPTION = (
    "A TL;DR, the decisions and the action items (owner, due) of the text you paste, each backed by a "
    "quote from that text, plus follow-ups on the same summary while it is still in the recent conversation."
)
SKILL_TAGS = ["summary", "tl;dr", "meeting notes", "transcript", "decisions", "action items", "owners"]
SKILL_EXAMPLES = [
    "Can you summarize this for me? <paste the transcript>",
    "TL;DR of this thread, focus on pricing: <paste the thread>",
    "What are the action items here? <paste the notes>",
    "Only the action items, with sources.",
]
