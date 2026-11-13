"""Pure: a canned answer for a message that is only a greeting or small talk, so it costs no LLM call.
Conservative on purpose: the WHOLE message must be made of the phrases below (in any order, with
punctuation), so anything that asks for something real goes on to the agent. A bare arithmetic question is
declined here too, so it is never answered, not even partly."""

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
_ARITHMETIC = re.compile(r"^\s*(?:what(?:'s| is)|how much is|calculate|compute)?\s*[\d\s.()+\-*/x×÷^=]+\??\s*$",
                         re.IGNORECASE)

INTRO = (
    "I turn a diff into a PR title, description, test plan and risk notes. Paste a `git diff` (or a .patch, or "
    "`git diff --stat` for a shallow draft) and, if you have them, the commit messages or ticket text that say why. "
    "I compute the counts and the risk level in code, ground every file and symbol in your diff, leave a visible "
    "placeholder when the why is missing, and follow a PR template you paste. I only draft: nothing is posted.\n\n"
    "Try:\n" + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
)
WELCOME = "You're welcome. " + INTRO
OFF_TOPIC_REPLY = "That's outside what I do, so I can't help with it here. " + INTRO


def reply_to(message: str) -> str | None:
    """The canned answer, or None when the message may be a real request."""
    if _ARITHMETIC.match(message or "") and re.search(r"\d", message or ""):
        return OFF_TOPIC_REPLY
    text = _SPACES.sub(" ", _NOT_WORD.sub(" ", (message or "").casefold())).strip()
    if not text:
        return INTRO
    if not _ONLY_SMALL_TALK.fullmatch(text):
        return None
    return WELCOME if _THANKS_ONLY.fullmatch(text) else INTRO
