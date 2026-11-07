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
    "I'm the failed payment rescuer. On request I look through your payments account for failed charges "
    "and invoices in the last 7 days (1 to 30 if you ask, or \"since my last run\" in this conversation), "
    "read each customer's plan and payment history, classify why the payment failed and how recoverable it "
    "is, and rank them, flagging the high-value ones. Then I draft a recovery email per failure and a chat "
    "digest of the high-value ones, and send each only after you approve that exact text. I never retry a "
    "charge or change a subscription. There is no automatic trigger: ask me whenever you want a run.\n\n"
    "Try:\n" + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
    + "\n\nFor customers at risk of leaving ask the churn early warning agent; for unpaid invoices ask the "
    "invoice chaser."
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
