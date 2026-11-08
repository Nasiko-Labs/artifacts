"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "social-post-drafter"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns an announcement, notes, a pasted article or a one-line idea into ready-to-copy public posts for "
    "LinkedIn and X: three variants with different angles, a hook, hashtags, a call to action and character "
    "counts computed in code (X counted the weighted way). It writes single LinkedIn posts, single X posts, "
    "X threads of up to 8 posts, or both. Paste a past post and it matches your voice for the rest of the "
    "conversation. It never invents a fact, number, customer name or quote: anything you didn't give it "
    "becomes a visible placeholder like [metric?]. It revises on request (\"punchier hook\", \"make variant 2 "
    "a thread\", \"no hashtags\"). It only drafts: it reads none of your data and never posts. Not for "
    "emails or private messages, ad copy, long articles, research or scheduling."
)

SKILL_ID = "draft-social-posts"
SKILL_NAME = "Draft social posts"
SKILL_DESCRIPTION = (
    "Three LinkedIn or X post variants (single post or thread) from the material you give, each with a "
    "different angle, in your voice, using only your facts; missing specifics become placeholders. Revise "
    "on request. Draft only: nothing is posted."
)
SKILL_TAGS = ["social media", "linkedin", "x", "thread", "announcement", "copywriting", "drafting"]
SKILL_EXAMPLES = [
    ("Draft a LinkedIn post announcing our v2 release: exports are now 10x faster, SSO for all plans, "
     "new audit log."),
    "Turn this changelog into an X thread: <paste the changelog>",
    "Write 3 post ideas about hiring a founding engineer; no hashtags.",
]
