"""Pure: canned answers that cost no LLM call: greetings and small talk, plus a few requests that are
plainly outside this agent's job (arithmetic, replying to the customer, assigning blame).
Conservative on purpose: for a greeting the WHOLE message must be small-talk phrases."""

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
_ARITHMETIC = re.compile(r"^\s*(?:what(?:'s| is)|calculate|compute|how much is)?\s*\(?\d[\d.]*\s*"
                         r"(?:[-+*/x×÷^]|plus|minus|times|divided by)\s*\d[\d.]*\)?\s*[=?.!]*\s*$", re.IGNORECASE)
_REPLY_TASK = re.compile(r"^\s*(?:please\s+)?(?:reply|respond|write back|answer|email|message|draft (?:a )?(?:reply|"
                         r"response|email))\b.*\b(?:customer|client|them|him|her|user)\b", re.IGNORECASE)
_BLAME = re.compile(r"\b(?:whose fault|who(?:'s| is| was)? (?:at fault|to blame|responsible for (?:the|this) "
                    r"(?:mess|failure|outage))|who screwed up|who messed up|blame)\b", re.IGNORECASE)

INTRO = (
    "I'm the escalation summarizer. Paste an escalation thread (ticket comments, a chat or email chain, "
    "internal notes) and I'll give the person it's escalated to a one-paragraph summary, the explicit ask "
    "(what's needed from them, by when) and the customer impact, only as the thread states it. Every "
    "statement is backed by a quote from the thread; if there's no ask or no stated impact, I say so instead "
    "of guessing. Then ask to show sources, only the ask, shorter, or for a given audience.\n\nTry:\n"
    + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
)
WELCOME = "You're welcome. " + INTRO
OFF_TOPIC_REPLY = "That's outside what I do, so I can't help with it here. " + INTRO
TOO_SHORT_REPLY = ("That's too short to summarize: I need an escalation thread of at least 30 words over two or "
                   "more messages, or 80 words in one block. " + INTRO)
NO_THREAD_YET = "You haven't pasted an escalation thread yet. " + INTRO
NO_BLAME_REPLY = ("I don't assign fault: the summary sticks to what the thread states, in neutral words. "
                  "Ask me what the thread says happened, or who said something, instead.")


def reply_to(message: str) -> str | None:
    """The canned answer, or None when the message may be a real request."""
    text = _SPACES.sub(" ", _NOT_WORD.sub(" ", (message or "").casefold())).strip()
    if not text:
        return INTRO
    if _ONLY_SMALL_TALK.fullmatch(text):
        return WELCOME if _THANKS_ONLY.fullmatch(text) else INTRO
    return None


def decline(message: str) -> str | None:
    """A fixed reply for a short request that is plainly out of scope, else None."""
    if _BLAME.search(message or ""):
        return NO_BLAME_REPLY
    if _ARITHMETIC.search(message or "") or _REPLY_TASK.search(message or ""):
        return OFF_TOPIC_REPLY
    return None
