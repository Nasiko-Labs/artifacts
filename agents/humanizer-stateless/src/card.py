"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "humanizer-stateless"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Takes a draft you paste (an email, a message, a memo, a post, or another agent's draft) and returns the "
    "same draft without AI-writing patterns: inflated phrases ('serves as a testament', 'delve'), chatbot "
    "openers and closers ('I hope this helps!'), filler, 'not only... but also', em-dash overuse and padded "
    "lists. It is a style pass, not a rewrite: every number, name, date, link, quote and the structure stay "
    "exactly as they were, checked in code; if a pass can't keep them, you get your draft back unchanged. It "
    "reports the AI-pattern density before and after (it never claims to beat AI detectors). Follow-ups: show "
    "the changes, keep my em dashes, more casual or formal, shorter, as json. Follow-ups work on what is still "
    "in the recent conversation; it keeps nothing between messages, so a long draft from earlier may need to "
    "be pasted again. Drafts over about 3k words are humanized up to that point and it says what was left out. "
    "English only; it writes nothing new, translates nothing and answers no general questions."
)

SKILL_ID = "humanize-draft"
SKILL_NAME = "Humanize a draft"
SKILL_DESCRIPTION = (
    "Removes AI-writing patterns from the draft you paste and keeps its facts, names, numbers, links, quotes "
    "and structure, with the AI-pattern density before and after and a list of changes."
)
SKILL_TAGS = ["humanize", "ai tone", "style", "polish", "editing", "writing", "draft"]
SKILL_EXAMPLES = [
    "Humanize this: <paste your draft>",
    "Can you make this sound more natural? <paste your draft>",
    "Remove the AI tone from this email, keep my em dashes: <paste the email>",
    "Show the changes",
]
