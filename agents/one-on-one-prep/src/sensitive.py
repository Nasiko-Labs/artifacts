"""Pure: personal or HR matters found by a fixed lexicon, sentence by sentence. Flagged sentences are left out
of everything the model sees unless the manager chooses to include them."""

from __future__ import annotations

import re

_TERMS = re.compile(
    r"medical|diagnos\w*|illness|\bill\b|surgery|cancer|pregnan\w*|maternity|paternity|divorce\w*|bereave\w*|"
    r"funeral|mental health|therapy|therapist|\bvisa\b|immigration|lawsuit|harass\w*|complain(?:t|ed|ts)|"
    r"grievance|disciplin\w*|termination|salary of|\bfired\b|laid off|\bunion\b|sick leave",
    re.IGNORECASE,
)
_CASE_SENSITIVE = re.compile(r"\bPIP\b")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")


def sentences(text: str) -> list[str]:
    return [m.group(0).strip() for m in _SENTENCE.finditer(text or "") if m.group(0).strip()]


def is_sensitive(sentence: str) -> bool:
    return bool(_TERMS.search(sentence) or _CASE_SENSITIVE.search(sentence))


def flagged(text: str) -> list[str]:
    return [s for s in sentences(text) if is_sensitive(s)]


def remove(text: str, drop: list[str]) -> str:
    """`text` without the flagged sentences (each removed once, where it stands)."""
    out = text or ""
    for sentence in drop:
        out = out.replace(sentence, "", 1)
    return re.sub(r"[ \t]{2,}", " ", out).strip()
