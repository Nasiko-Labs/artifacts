"""Pure: what a message about the stored story asks for.

    "one sentence", "shorter", "headline"       → headline        (no call)
    "longer", "more detail"                     → longer          (no call: the template at default length)
    "show the numbers", "show sources"          → numbers         (no call)
    "as json", "json only"                      → json            (no call)
    "for execs", "for the sales team", "simpler" → rewrite        (one write call, same facts)
    anything else                               → question        (one scoped call)
"""

from __future__ import annotations

import re

from request import names_audience

COMMAND_MAX_WORDS = 12
_WORDS = re.compile(r"[^a-z0-9;' ]+")
_FILLER = re.compile(r"\b(?:please|pls|can you|could you|would you|show me|show|give me|just|only|me|now|again|"
                     r"the|a|an|make it|make that|it|that|in|as|version|write|say|do|i want|i'd like)\b")
_COMMANDS = (
    ("headline", re.compile(r"^(?:one sentence|1 sentence|single sentence|shorter|short|headline|tl;?dr|"
                            r"one liner|brief|briefer)$")),
    ("longer", re.compile(r"^(?:longer|more detail|more details|long|expand|more|full story|in detail)$")),
    ("numbers", re.compile(r"^(?:numbers|facts|sources|working|your working|data|figures|computed facts|"
                           r"numbers behind (?:this|story))$")),
    ("json", re.compile(r"^(?:json|json only)$")),
)


def _plain(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").casefold()).split())


def parse(text: str) -> str:
    """One of: headline, longer, numbers, json, rewrite, question."""
    plain = _plain(text)
    if plain and len(plain.split()) <= COMMAND_MAX_WORDS and names_audience(text):
        return "rewrite"
    if plain and len(plain.split()) <= COMMAND_MAX_WORDS:
        core = " ".join(_FILLER.sub(" ", plain).split())
        for view, pattern in _COMMANDS:
            if pattern.match(core):
                return view
    return "question"
