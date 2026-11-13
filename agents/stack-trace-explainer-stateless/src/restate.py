"""Pure: does a follow-up answer mostly copy the previous reply instead of answering the new question?
(Mirrors the Rust twin's restate.rs.) Measured as the share of the answer's word trigrams that also occur in
the previous reply, its italic note lines left out. At or above RESTATE_OVERLAP the follow-up call is retried
once."""

from __future__ import annotations

import re

from relevance import prose_lines

RESTATE_OVERLAP = 0.5
NGRAM = 3
_WORD = re.compile(r"[A-Za-z0-9_]+")


def _trigrams(text: str) -> list[str]:
    words = [w.casefold() for w in _WORD.findall(text or "")]
    return [" ".join(words[i:i + NGRAM]) for i in range(len(words) - NGRAM + 1)]


def overlap(answer: str, previous: str) -> float:
    """The share (0.0-1.0) of `answer`'s trigrams found in `previous`; 0.0 when either is too short."""
    seen = set(_trigrams("\n".join(prose_lines(previous))))
    mine = _trigrams(answer)
    if not mine or not seen:
        return 0.0
    return sum(1 for t in mine if t in seen) / len(mine)


def restates(answer: str, previous: str) -> bool:
    return overlap(answer, previous) >= RESTATE_OVERLAP
