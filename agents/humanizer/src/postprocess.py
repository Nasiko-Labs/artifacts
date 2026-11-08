"""Pure, deterministic cleanups on the model's (still masked) text, so protected spans are never touched:
chatbot openers/closers the model missed, the em-dash budget, quote style, doubled spaces, the trailing
newline."""

from __future__ import annotations

import re

from patterns import em_dash_budget, word_count
from patterns_phrases import EM_DASH

_OPENER = re.compile(r"\A\s*(?:(?:certainly|absolutely|of course|sure)[!,.]\s*)?(?:here(?:'|’)?s|here is) "
                     r"(?:the|a|your) [^:\n]{0,60}:\s*|\A\s*(?:certainly|absolutely|of course)!\s*", re.IGNORECASE)
_CLOSER = re.compile(r"\s*I hope this helps[^.!?\n]*[.!?]*\s*\Z", re.IGNORECASE)
_DASH = re.compile(rf"[ \t]*(?:{EM_DASH})[ \t]*")
_SPACES = re.compile(r"(?<=\S)[ \t]{2,}")
_CURLY = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def postprocess(model_text: str, source: str, keep_em_dashes: bool) -> str:
    text = strip_artefacts(model_text, source)
    if not keep_em_dashes:
        text = limit_em_dashes(text, em_dash_budget(word_count(source)))
    if straight_dominant(source):
        text = text.translate(_CURLY)
    text = _SPACES.sub(" ", text).strip()
    return text + "\n" if source.endswith("\n") else text


def strip_artefacts(text: str, source: str) -> str:
    """A leading "Certainly! Here's the revised text:" or a closing "I hope this helps!" the model added or
    kept; never one the draft itself opens with (a reply that starts "Sure, ..." is the user's own)."""
    stripped = text
    opener = _OPENER.match(stripped)
    if opener and opener.group(0).strip() and not _OPENER.match(source.strip()):
        stripped = stripped[opener.end():]
    return _CLOSER.sub("", stripped) if stripped.strip() else text


def limit_em_dashes(text: str, budget: int) -> str:
    """Keeps the first `budget` dashes; each later one becomes ", " before a lowercase word, else ". "."""
    kept = 0
    out: list[str] = []
    cursor = 0
    for m in _DASH.finditer(text):
        out.append(text[cursor:m.start()])
        cursor = m.end()
        if kept < budget:
            kept += 1
            out.append(m.group(0))
            continue
        following = text[m.end():m.end() + 1]
        if not following or following == "\n":
            out.append(".")
        elif following.islower():
            out.append(", ")
        else:
            out.append(". ")
    out.append(text[cursor:])
    return "".join(out)


def straight_dominant(source: str) -> bool:
    straight = source.count('"') + source.count("'")
    curly = sum(source.count(c) for c in "“”‘’")
    return straight >= curly
