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
_WRITE_REQUEST = re.compile(
    r"\b(?:re-?write|write|draft|create|generate|make|compose|fix|improve|edit|polish)\b(?:\s+\w+){0,4}?\s+"
    r"(?:prd|spec|requirements? doc|product requirements|it|this|that)\b", re.IGNORECASE)

INTRO = (
    "I'm the PRD critic. Paste a PRD (at least 150 words) and I'll return its gaps: missing sections, vague or "
    "untestable requirements, success metrics with no number, window or baseline, placeholders and unanswered "
    "questions, plus the questions a reviewer will ask, each tied to a line of your PRD. A fixed rubric gives "
    "it a 0-100 score, so the same PRD always gets the same number. Add \"lenient\" or \"focus: metrics\" on the "
    "first line if you like. I never rewrite the PRD or judge the idea, and I send nothing.\n\nTry:\n"
    + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
)
WELCOME = "You're welcome. " + INTRO
OFF_TOPIC_REPLY = "That's outside what I do, so I can't help with it here. " + INTRO
WRITE_REPLY = ("I only review PRDs; I don't write or rewrite them (the PRD Writer agent does that). Each gap I "
               "report carries a one-line fix you can apply yourself. " + INTRO)


def reply_to(message: str) -> str | None:
    """The canned answer, or None when the message may be a real request."""
    text = _SPACES.sub(" ", _NOT_WORD.sub(" ", (message or "").casefold())).strip()
    if not text:
        return INTRO
    if not _ONLY_SMALL_TALK.fullmatch(text):
        return None
    return WELCOME if _THANKS_ONLY.fullmatch(text) else INTRO


def asks_to_write(message: str) -> bool:
    """A short request to write or rewrite a PRD: out of scope, declined in code."""
    return bool(_WRITE_REQUEST.search(message or ""))
