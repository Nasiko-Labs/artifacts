"""Pure: a canned answer for a message that is only a greeting or small talk, so it costs no LLM call,
and the other fixed replies (off topic, private message, "post it for me"). Conservative on purpose:
the WHOLE message must be made of the phrases below, so anything that asks for something real goes to
the graph."""

from __future__ import annotations

import re

from card import SKILL_EXAMPLES

_GREETINGS = (r"hi", r"hii+", r"hello", r"hey", r"hiya", r"howdy", r"yo", r"greetings",
              r"good (?:morning|afternoon|evening|day)", r"(?:hi|hello|hey) there")
_THANKS = (r"thanks", r"thank you", r"thx", r"ty", r"cheers", r"thanks a lot", r"thank you so much", r"many thanks",
           r"great", r"cool", r"nice", r"perfect")
_ABOUT = (r"who are you", r"what are you", r"what do you do", r"what can you do", r"help", r"how can you help(?: me)?",
          r"what is this", r"how does this work", r"how are you")
_PHRASE = "|".join((*_THANKS, *_ABOUT, *_GREETINGS))
_ONLY_SMALL_TALK = re.compile(rf"(?:{_PHRASE})(?: (?:{_PHRASE}))*")
_THANKS_ONLY = re.compile(rf"(?:{'|'.join(_THANKS)})(?: (?:{'|'.join(_THANKS)}))*")
_NOT_WORD = re.compile(r"[^a-z' ]+")
_SPACES = re.compile(r"\s+")

INTRO = (
    "I'm the social post drafter. Give me an announcement, notes, a pasted article or an idea and I write "
    "three public post variants for LinkedIn or X (single post or thread), each with a different angle, "
    "with character counts. I use only the facts you give me: anything specific you didn't tell me becomes "
    "a placeholder like [metric?]. Paste a past post and I'll match your voice. I only draft; you copy a "
    "variant and post it yourself.\n\nTry:\n"
    + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
)
WELCOME = "You're welcome. " + INTRO
OFF_TOPIC_REPLY = "That's outside what I do, so I can't help with it here. " + INTRO
PRIVATE_REPLY = (
    "I write public posts for LinkedIn and X, not emails or private messages to a person or group; that's the "
    "Drafter's job. " + INTRO
)
PUBLISH_REPLY = ("I only draft; copy a variant and post it yourself. I can't post, schedule or publish "
                 "anything. Say \"punchier hook\", \"shorter\" or \"make variant 2 a thread\" to change a draft.")


def reply_to(message: str) -> str | None:
    """The canned answer, or None when the message may be a real request."""
    text = _SPACES.sub(" ", _NOT_WORD.sub(" ", (message or "").casefold())).strip()
    if not text:
        return INTRO
    if not _ONLY_SMALL_TALK.fullmatch(text):
        return None
    return WELCOME if _THANKS_ONLY.fullmatch(text) else INTRO
