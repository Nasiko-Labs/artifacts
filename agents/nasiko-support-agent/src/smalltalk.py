"""Pure: a canned answer for a message that is only a greeting or small talk, so it costs no LLM call.
Conservative on purpose: the WHOLE message must be made of the phrases below (in any order, with
punctuation), so anything that asks for something real goes on to the agent."""

from __future__ import annotations

import re

from card import SKILL_EXAMPLES

_GREETINGS = (r"hi", r"hii+", r"hello", r"hey", r"hiya", r"howdy", r"yo", r"greetings",
              r"good (?:morning|afternoon|evening|day)", r"(?:hi|hello|hey) there")
_THANKS = (r"thanks", r"thank you", r"thx", r"ty", r"cheers", r"thanks a lot", r"thank you so much", r"many thanks",
           r"great", r"cool", r"nice", r"perfect", r"ok", r"okay", r"got it")
_ABOUT = (r"who are you", r"what are you", r"what do you do", r"what can you do", r"help", r"how can you help(?: me)?",
          r"what is this", r"how does this work", r"how are you")
_PHRASE = "|".join((*_THANKS, *_ABOUT, *_GREETINGS))
_ONLY_SMALL_TALK = re.compile(rf"(?:{_PHRASE})(?: (?:{_PHRASE}))*")
_THANKS_ONLY = re.compile(rf"(?:{'|'.join(_THANKS)})(?: (?:{'|'.join(_THANKS)}))*")
_NOT_WORD = re.compile(r"[^a-z' ]+")
_SPACES = re.compile(r"\s+")

INTRO = (
    "I'm the Nasiko support agent. Ask me how to do something in Nasiko and I'll tell you where to click, the "
    "steps, and the matching `nasiko` command, with a link to the docs. If something isn't working, tell me what "
    "you see or paste the error or log lines, and I'll find the likely cause and the fix. I answer from my "
    "Nasiko notes only, and I explain rather than change anything for you.\n\nTry:\n"
    + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
)
WELCOME = "You're welcome. Anything else about Nasiko? For example: " + SKILL_EXAMPLES[2]
OFF_TOPIC_REPLY = (
    "That's outside what I help with: I only answer questions about using Nasiko (how to do something, or why "
    f"something isn't working). For example: \"{SKILL_EXAMPLES[0]}\""
)


def reply_to(message: str) -> str | None:
    """The canned answer, or None when the message may be a real request."""
    text = _SPACES.sub(" ", _NOT_WORD.sub(" ", (message or "").casefold())).strip()
    if not text:
        return INTRO if not (message or "").strip() else None
    if not _ONLY_SMALL_TALK.fullmatch(text):
        return None
    return WELCOME if _THANKS_ONLY.fullmatch(text) else INTRO
