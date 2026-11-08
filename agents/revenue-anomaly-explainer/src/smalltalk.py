"""Pure: a canned answer for a message that is only a greeting or small talk, so it costs no tool or LLM call.
Conservative on purpose: the WHOLE message must be made of the phrases below (in any order, with punctuation), so
anything that asks for something real goes to the graph. (Copied in once from the hardened kit; owned by this
agent; intro text is this agent's.)"""

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
    "I'm the revenue anomaly explainer. On request I read the subscriptions, plan changes and failed payments in "
    "your payments account and check a day (yesterday by default, or a date, or the last few days) against the "
    "previous 28 days: new, churned, expansion, contraction and net new MRR and failed payments, per currency. "
    "Every number and z-score is computed in code; I flag only moves that are both statistically unusual and big "
    "enough to matter, name the accounts behind them with the facts the data shows (a cancellation reason, a plan "
    "change, a decline code), never a guessed cause, and draft a short chat post that goes out only after you "
    "approve the exact text. I never change anything in your payments account. To work through failed payments "
    "one by one, a failed-payment rescuer agent is the better fit.\n\n"
    "Try:\n" + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
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
