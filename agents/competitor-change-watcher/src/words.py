"""Pure: word tokens for overlap checks (classification `what` and roadmap mapping), the normalised form
used to compare entries across pastes, and an entry's first sentence."""

from __future__ import annotations

import hashlib
import re

_WORD = re.compile(r"[a-z0-9][a-z0-9+#]*")
_SPACES = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
MAX_SENTENCE_CHARS = 160
STOPWORDS = frozenset({
    "the", "and", "for", "with", "you", "your", "our", "now", "new", "are", "can", "from", "into", "all", "this",
    "that", "its", "has", "have", "was", "will", "not", "but", "more", "use", "via", "per", "any", "out", "get",
    "a", "an", "or", "to", "of", "in", "on", "by", "is", "it", "as", "at", "be", "we", "so", "up",
})


def tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in _WORD.findall(str(text or "").casefold()) if w not in STOPWORDS and len(w) > 1)


def norm(text: str) -> str:
    return _SPACES.sub(" ", str(text or "")).strip().casefold()


def fingerprint(text: str) -> str:
    """Stable id of an entry's text: punctuation, case and spacing do not change it."""
    plain = " ".join(re.findall(r"[a-z0-9]+", str(text or "").casefold()))
    return hashlib.sha1(plain.encode()).hexdigest()[:16]


def first_sentence(text: str) -> str:
    line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
    sentence = _SENTENCE_END.split(line, maxsplit=1)[0]
    return sentence if len(sentence) <= MAX_SENTENCE_CHARS else sentence[:MAX_SENTENCE_CHARS].rstrip() + "..."


def overlap(part: str, whole: str) -> float:
    """Share of `part`'s tokens that appear in `whole`."""
    mine = tokens(part)
    return len(mine & tokens(whole)) / len(mine) if mine else 0.0
