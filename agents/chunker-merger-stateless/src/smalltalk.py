"""Pure: a canned answer for a message that is only a greeting or small talk, so it costs nothing.
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
    "I'm the Chunker + Merger. I do two things, in code, with no model. Split: paste a long text and say how "
    "to cut it (into 4 parts, parts of 8,000 chars, by speaker turns, by headings) and optionally what to do "
    "with each part; I return numbered, ready-to-paste prompts that rejoin to your text exactly, plus a merge "
    "instruction. Merge: paste two or more JSON results from the parts and I merge them into one result with "
    "the same schema, removing exact duplicates. A text longer than one message can arrive in pieces "
    "(\"part 1 of 3, more coming\", then \"that's all\").\n\nTry:\n"
    + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
)
WELCOME = "You're welcome. " + INTRO


def reply_to(message: str) -> str | None:
    """The canned answer, or None when the message may be a real request."""
    text = _SPACES.sub(" ", _NOT_WORD.sub(" ", (message or "").casefold())).strip()
    if not text:
        return INTRO
    if not _ONLY_SMALL_TALK.fullmatch(text):
        return None
    return WELCOME if _THANKS_ONLY.fullmatch(text) else INTRO
