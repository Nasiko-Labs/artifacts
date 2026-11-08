"""Pure: what the user's instruction asks of the story — the audience and the length — read in code."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

DEFAULT_AUDIENCE = "someone who doesn't work with data"
EXEC_AUDIENCE = "executives"
SIMPLER_AUDIENCE = "someone new to the topic, in the simplest everyday words"
LENGTH_CAPS = {"one": 30, "short": 60, "default": 150, "exec": 100}
LENGTH_GUIDE = {"one": "exactly one sentence of at most 30 words (the headline only; leave narrative empty)",
                "short": "at most 60 words in total",
                "default": "about 90 to 150 words in total",
                "exec": "at most 100 words in total"}

_ONE = re.compile(r"\b(?:one|1|a single|single)[- ]sentence\b|\bin a sentence\b|\bheadline only\b|\bone[- ]liner\b",
                  re.IGNORECASE)
_SHORT = re.compile(r"\b(?:short|shorter|brief|briefly|quick|concise|tl;?dr)\b", re.IGNORECASE)
_LONG = re.compile(r"\b(?:paragraph|longer|detailed|in detail|more detail)\b", re.IGNORECASE)
_EXEC = re.compile(r"\b(?:execs?|executives?|leadership|the board|board members|ceo|cfo|coo|c-suite|management|"
                   r"my boss|senior leaders?)\b", re.IGNORECASE)
_SIMPLER = re.compile(r"\b(?:simpler|simple words|plainer|plain english|plain language|no jargon|eli5|"
                      r"like i'?m (?:five|5))\b", re.IGNORECASE)
_FOR = re.compile(r"\bfor (?:the |my |our |a |an )?([a-z][\w&' -]{1,40}?)\s*(?=[:,.;!?\n]|$|\bno\b|\bin\b|\bwith\b|"
                  r"\bplease\b)", re.IGNORECASE)
_NOT_AUDIENCE = re.compile(r"^(?:this|that|it|me|us|them|the table|this table|these numbers|now|example|"
                           r"each|every|instance|the data)$", re.IGNORECASE)


@dataclass(frozen=True)
class Ask:
    audience: str = DEFAULT_AUDIENCE
    length: str = "default"        # one | short | default | exec
    instruction: str = ""          # the user's own words around the table (may supply causes or units)

    @property
    def cap(self) -> int:
        return LENGTH_CAPS[self.length]

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict | None) -> Ask:
        data = data or {}
        length = str(data.get("length") or "default")
        return Ask(str(data.get("audience") or DEFAULT_AUDIENCE), length if length in LENGTH_CAPS else "default",
                   str(data.get("instruction") or ""))


_QUESTION_START = re.compile(r"^\s*(?:what|why|how|which|who|when|where|is|are|was|were|did|does|do)\b", re.IGNORECASE)
_LEADING_FOR = re.compile(r"^\s*(?:(?:now|ok|okay|and|then|rewrite|redo|explain|write|make|say|tell|put)\b[\s,]*"
                          r"(?:it|this|that)?\s*)*(?:for|to)\s+", re.IGNORECASE)


def names_audience(text: str) -> bool:
    """True when a follow-up asks for a different reader or simpler words ("for execs", "now for the sales
    team", "simpler"); a question about the table ("what was revenue for West?") is not one."""
    text = text or ""
    if _QUESTION_START.match(text):
        return False
    return bool(_EXEC.search(text) or _SIMPLER.search(text) or (_LEADING_FOR.match(text) and _audience_phrase(text)))


def read_ask(instruction: str, base: Ask | None = None) -> Ask:
    """The audience and length the instruction asks for; anything it doesn't say comes from `base`."""
    text = instruction or ""
    base = base or Ask()
    audience = base.audience
    if _EXEC.search(text):
        audience = EXEC_AUDIENCE
    elif _SIMPLER.search(text):
        audience = SIMPLER_AUDIENCE
    elif phrase := _audience_phrase(text):
        audience = phrase
    length = base.length if base.length != "exec" else "default"
    if _ONE.search(text):
        length = "one"
    elif _SHORT.search(text):
        length = "short"
    elif _LONG.search(text):
        length = "default"
    if audience == EXEC_AUDIENCE and length == "default":
        length = "exec"
    return Ask(audience, length, "\n".join(x for x in (base.instruction, text.strip()) if x))


def _audience_phrase(text: str) -> str:
    match = _FOR.search(text)
    if not match:
        return ""
    phrase = " ".join(match.group(1).split()).strip(" -'")
    return "" if _NOT_AUDIENCE.match(phrase) else phrase
