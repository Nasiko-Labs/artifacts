"""Pure: is a fragment really in the user's words? Used to keep assumptions, constraints and owners tied to
what the user typed (never to org policy or to the model's own text)."""

from __future__ import annotations

import re

_SPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9]+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
MIN_FRAGMENT_CHARS = 3
CONTENT_TOKEN_CHARS = 3
GROUNDED_SHARE = 0.6
_STOP = frozenset({"the", "and", "for", "with", "that", "this", "are", "our", "from", "will", "must", "should",
                   "have", "has", "not", "all", "any", "but", "into", "per", "can", "use", "using"})


def norm(text: str) -> str:
    return _SPACE.sub(" ", (text or "").translate(_QUOTES).casefold()).strip().strip(".,;:!?\"'")


def is_verbatim(fragment: str, source: str) -> bool:
    """The fragment, normalised, appears in the source, normalised."""
    piece = norm(fragment)
    return len(piece) >= MIN_FRAGMENT_CHARS and piece in norm(source)


def content_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(norm(text)) if len(t) >= CONTENT_TOKEN_CHARS and t not in _STOP}


def mostly_grounded(text: str, source: str) -> bool:
    """At least 60% of the text's content words appear in the source (or the text is verbatim)."""
    if is_verbatim(text, source):
        return True
    tokens = content_tokens(text)
    if not tokens:
        return False
    found = content_tokens(source)
    return len(tokens & found) / len(tokens) >= GROUNDED_SHARE
