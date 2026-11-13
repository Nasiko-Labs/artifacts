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
    "I'm the humanizer. Paste a draft (an email, a message, a memo, a post) and I'll return the same draft "
    "without AI-writing patterns: inflated phrases, chatbot openers and closers, filler, em-dash overuse and "
    "padded lists. Your facts, names, numbers, links, quotes and structure stay exactly as they were, and I show "
    "the AI-pattern density before and after. Then ask to show the changes, keep your em dashes, make it more "
    "casual or formal, or shorter.\n\nTry:\n"
    + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
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


_ARITHMETIC = re.compile(r"(?:what(?:'s|’s| is)|calculate|compute|how much is|solve)?\s*[\d\s.,+\-*/x×÷^()=]+\??",
                         re.IGNORECASE)


def is_arithmetic(message: str) -> bool:
    """A bare sum ("what's 2+2?"): out of scope, declined in code with no call."""
    text = (message or "").strip()
    return bool(_ARITHMETIC.fullmatch(text)) and any(c.isdigit() for c in text) and any(c in "+-*/x×÷^" for c in text)
