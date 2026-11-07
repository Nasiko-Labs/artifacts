"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from the drafter: workflow generation sees only the name and
description."""

AGENT_NAME = "reply-drafter"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Drafts your reply to a message you received, in your own voice. Paste the incoming email, chat or text "
    "message, say what you want to say back (\"say yes but not before Thursday\", \"decline politely\") and "
    "paste 1-3 of your past replies: it measures your style from them (length, greeting, sign-off, "
    "contractions, exclamation marks, emoji) and checks the reply against those numbers. You get the reply, "
    "a shorter alternative and notes on how the tone was matched. It never invents a fact: anything specific "
    "you didn't give it becomes a placeholder like [date?], and it never accepts, declines or promises more "
    "than you said. It only drafts and sends nothing. For a new message with nothing to reply to, use the "
    "drafter. Not for answering questions, essays, code or translation."
)

SKILL_ID = "draft-reply"
SKILL_NAME = "Draft a reply in your tone"
SKILL_DESCRIPTION = (
    "A reply to a pasted email, chat or text message, written in the style measured from your past replies, "
    "with a shorter alternative and tone notes; missing specifics become placeholders. Revise it on request. "
    "Draft only: nothing is sent."
)
SKILL_TAGS = ["reply", "drafting", "email", "chat message", "tone match", "writing style"]
SKILL_EXAMPLES = [
    "Reply to this email: <paste> My past replies: <1-3 examples>. Say yes but not before Thursday.",
    "Reply to this Slack message: <paste>, decline politely",
    "Make it shorter",
]
