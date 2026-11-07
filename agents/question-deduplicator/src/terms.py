"""Pure: the distinctive terms of a text, and term overlap (Jaccard). Used for the shortlist score and the
strength gate, so a small model's "same question" is checked against the words actually shared."""

from __future__ import annotations

import re
from collections.abc import Iterable

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]|[a-z0-9]")
_STOP_WORDS = """
a about after again all already also am an and any anybody anyone are as ask asked asking at be been before being
but by can could did do does doing done for from get got had has have how i if in into is it its just know let me my
need no not of on or our please question should so some somebody someone tell than that the their them then there
these they this to us was we were what when where whether which who why will with would yes you your hi hey thanks
check checked discussed discuss mentioned anything ever way ways
"""
_STOP = frozenset(_STOP_WORDS.split())
LITERAL = re.compile(r"[A-Za-z_]+[0-9_.-][A-Za-z0-9_.-]*")


def terms(text: str) -> frozenset[str]:
    out = set()
    for token in _TOKEN.findall((text or "").casefold()):
        if token in _STOP:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        out.add(token)
    return frozenset(out)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left, right = set(a), set(b)
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def literals(text: str) -> tuple[str, ...]:
    """Distinctive tokens (error codes, identifiers such as eu-west-1, ERR_42) kept verbatim, in order."""
    found = [t.strip("._-") for t in LITERAL.findall(text or "")]
    return tuple(dict.fromkeys(t for t in found if len(t) >= 3 and any(c.isdigit() or c in "_-" for c in t)))
