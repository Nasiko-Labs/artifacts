"""Pure: a canned answer for a message that is only a greeting or small talk, so it costs no tool or
LLM call. Conservative on purpose: the WHOLE message must be made of the phrases below (in any
order, with punctuation), so anything that asks for something real goes to the graph.
(Copied in once from the hardened kit; owned by this agent; intro text is this agent's.)"""

from __future__ import annotations

import re

from card import SKILL_EXAMPLES

_GREETINGS = (r"hi", r"hii+", r"hello", r"hey", r"hiya", r"howdy", r"yo", r"greetings",
              r"good (?:morning|afternoon|evening|day)", r"(?:hi|hello|hey) there")
_THANKS = (r"thanks", r"thank you", r"thx", r"ty", r"cheers", r"thanks a lot", r"thank you so much", r"many thanks",
           r"great", r"cool", r"nice")
_ABOUT = (r"who are you", r"what are you", r"what do you do", r"what can you do", r"help", r"how can you help(?: me)?",
          r"what is this", r"how does this work", r"how are you")
_PHRASE = "|".join((*_THANKS, *_ABOUT, *_GREETINGS))
_ONLY_SMALL_TALK = re.compile(rf"(?:{_PHRASE})(?: (?:{_PHRASE}))*")
_THANKS_ONLY = re.compile(rf"(?:{'|'.join(_THANKS)})(?: (?:{'|'.join(_THANKS)}))*")
_NOT_WORD = re.compile(r"[^a-z' ]+")
_SPACES = re.compile(r"\s+")

INTRO = (
    "I turn emails that are really work requests into tracker issues, after you approve each one. I scan "
    "your inbox from the last 3 days (1 to 14 if you ask), propose one issue per request with the asker's "
    "exact words and a link back to the email, flag anything already tracked, and create each issue only "
    "after you approve it. Then I draft a short \"tracked, here's the link\" reply and send it only after "
    "you approve that too. I never invent a priority, assignee or due date.\n\nTry:\n"
    + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
    + "\n\nNo inbox connected? Paste one email (Subject, From, Date and the body) and I'll propose its issue."
)
WELCOME = "You're welcome. " + INTRO
OFF_TOPIC_REPLY = "That's outside what I do, so I can't help with it here. " + INTRO


def reply_to(message: str) -> str | None:
    """The canned answer, or None when the message may be a real request."""
    text = _SPACES.sub(" ", _NOT_WORD.sub(" ", (message or "").casefold())).strip()
    if not text:
        return INTRO
    if not _ONLY_SMALL_TALK.fullmatch(text):
        return None
    return WELCOME if _THANKS_ONLY.fullmatch(text) else INTRO
