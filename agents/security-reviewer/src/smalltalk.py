"""Pure: canned answers that cost no LLM call: small talk, and requests for exploits or attacks.

Small talk is conservative on purpose: the WHOLE message must be made of the phrases below (in any order,
with punctuation), so anything that asks for something real goes on to the agent."""

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

_EXPLOIT = re.compile(
    r"\b(?:write|give|generate|craft|build|create|make|show|send|need|want)\b[^.\n]{0,40}?"
    r"\b(?:exploits?|payloads?|poc|proof[- ]of[- ]concept|reverse shell|shellcode|attack (?:script|tool|code)|"
    r"malware|backdoor|keylogger)\b"
    r"|\bhow (?:do|can|would|could|should) (?:i|we|you|one) (?:attack|exploit|hack|break into|pwn|compromise)\b"
    r"|\b(?:hack|attack|probe|pentest|pen-test|scan|fuzz|ddos|exploit)\s+(?:this|that|the|their|our|my|a|an)\s+"
    r"(?:site|website|server|system|host|ip|url|app|application|network|endpoint|domain|company|api)\b"
    r"|\bexploit (?:this|it|that|finding|the (?:sql|bug|vuln|injection|finding))",
    re.IGNORECASE,
)

INTRO = (
    "I review code and diffs for security problems and suggest fixes. Paste source code (Python, JS/TS, Go, "
    "Java, Ruby, PHP, C#, shell, SQL, Dockerfile, YAML/JSON config), a unified diff, or a dependency manifest "
    "such as requirements.txt or package.json. Rules in code find candidates and a secret scan masks every "
    "secret before the model sees the code; the model then triages each candidate and looks for logic flaws "
    "like missing authorization. Each finding gets a severity, a file:line location, the quoted line and a fix. "
    "Pasted code is sent to the model provider configured for this workspace. I never run code, write exploits "
    "or probe systems.\n\nTry:\n" + "\n".join(f"- {example}" for example in SKILL_EXAMPLES)
)
WELCOME = "You're welcome. " + INTRO
OFF_TOPIC_REPLY = "That's outside what I do, so I can't help with it here. " + INTRO
EXPLOIT_REPLY = "I review your code and explain fixes; I don't write exploits or test other systems. " + INTRO


def reply_to(message: str) -> str | None:
    """The canned answer, or None when the message may be a real request."""
    text = _SPACES.sub(" ", _NOT_WORD.sub(" ", (message or "").casefold())).strip()
    if not text:
        return INTRO
    if not _ONLY_SMALL_TALK.fullmatch(text):
        return None
    return WELCOME if _THANKS_ONLY.fullmatch(text) else INTRO


def asks_for_attack(instruction: str) -> bool:
    """True when the user's own words (never the pasted code) ask for an exploit, payload or an attack."""
    return bool(_EXPLOIT.search(instruction or ""))
