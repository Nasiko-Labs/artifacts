"""Pure: a canned answer for a message that is only a greeting or small talk, so it costs no LLM call.
Conservative on purpose: the WHOLE message must be made of the phrases below (in any order, with
punctuation), so anything that asks for something real goes on to the agent."""

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
    "I'm the macro gap finder for your support desk. I read the agent replies on recently solved tickets "
    "(last 30 days by default, up to 60 tickets), find the answers your agents keep typing by hand, count how "
    "often each one occurs (in code, by text similarity) and draft a macro for each, skipping what your "
    "existing macros already cover. Customer names, emails, phone numbers and ticket numbers are replaced by "
    "placeholders before any drafting, and a draft only says what at least two of its replies say. No desk "
    "connected? Paste 10 or more replies (separated by --- or a CSV export) instead. I only read: I never "
    "create macros or edit tickets, and I don't answer customers.\n\nTry:\n"
    + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
)
WELCOME = "You're welcome. " + INTRO
NO_REPLIES_YET = "Ask me to read your support desk, or paste the replies. " + INTRO
OFF_TOPIC_REPLY = "That's outside what I do, so I can't help with it here. " + INTRO
FOLLOWUP_HELP = (
    "I can work on the last result: \"show the replies behind 2\", \"min 5\" or \"ignore anything under 5 "
    "replies\", \"top 3\", \"shorter draft for 1\", \"friendlier draft for 2\", \"list the boilerplate you "
    "ignored\", or \"as json\". Or paste a new set of replies."
)


def reply_to(message: str) -> str | None:
    """The canned answer, or None when the message may be a real request."""
    text = _SPACES.sub(" ", _NOT_WORD.sub(" ", (message or "").casefold())).strip()
    if not text:
        return INTRO
    if not _ONLY_SMALL_TALK.fullmatch(text):
        return None
    return WELCOME if _THANKS_ONLY.fullmatch(text) else INTRO
