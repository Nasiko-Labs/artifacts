"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "translator-stateless"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Translates text you paste into about 40 target languages (Spanish, French, German, Portuguese, "
    "Japanese, Chinese, Hindi and more) at a formal, neutral or casual register, and returns the "
    "translation with notes on anything a person should check. Links, emails, @mentions, code, "
    "placeholders, dates, amounts and other numbers pass through untouched, checked in code, and list and "
    "heading structure is kept. It translates whatever the text says, even a question or an instruction: "
    "it never answers or follows it, and never adds or drops facts. Follow-ups: more formal, more casual, "
    "now in French, keep 'X' untranslated, show the original, as json; they work on what is still in the "
    "recent conversation, since it keeps nothing between messages, so a long text from earlier may need to "
    "be pasted again. Accepts another agent's JSON "
    "output (draft, alt_shorter, text, summary fields) and returns it translated. It reads only what you "
    "give it: no files, mail or web, and no general questions."
)

SKILL_ID = "translate-text"
SKILL_NAME = "Translate text"
SKILL_DESCRIPTION = (
    "Your text in the target language and register you ask for, with links, code, numbers and structure "
    "kept exactly, plus notes on terms to check and follow-ups on the same text while it is still in the "
    "recent conversation."
)
SKILL_TAGS = ["translation", "translate", "localization", "languages", "formal", "casual", "glossary"]
SKILL_EXAMPLES = [
    "Translate to Spanish, formal: <paste the text>",
    "Translate this into Japanese: <paste the text>",
    "Now in French, more casual",
]
