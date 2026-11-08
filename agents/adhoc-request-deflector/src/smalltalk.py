"""Pure: a canned answer for a message that is only a greeting or small talk, so it costs no tool or
LLM call. Conservative on purpose: the WHOLE message must be made of the phrases below (in any
order, with punctuation), so anything that asks for something real goes to the graph.
Copied in once from the MCP kit; owned by this agent; the intro is this agent's own."""

from __future__ import annotations

import re

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
    "I check whether a dashboard already answers a data request and draft the reply with the link. I read your "
    "dashboard index (a sheet titled like \"Dashboard Index\", or a list you paste), label the match exact, "
    "partial or none, and never answer the data question myself. Name a chat channel and I'll scan its recent "
    "requests; a reply is posted in a thread only after you approve it.\n\nTry: 'can you pull weekly active "
    "users by plan?', 'check #data-requests since Monday', 'is there a dashboard for churn?'"
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
