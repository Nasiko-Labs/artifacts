"""Pure: vague words in requirements (whole word, case-insensitive) and contradictory pairs. Each finding
becomes an open question; the model never decides what is vague."""

from __future__ import annotations

import re
from collections.abc import Sequence

VAGUE = ("fast", "quick", "quickly", "scalable", "highly available", "secure", "robust", "reliable", "real-time",
         "real time", "user-friendly", "intuitive", "simple", "easy", "large", "many", "high volume",
         "low latency", "seamless", "seamlessly", "flexible", "modern", "efficient", "etc", "and so on",
         "as needed", "tbd", "appropriate", "reasonable")
CANONICAL = {"quickly": "quick", "real time": "real-time", "seamlessly": "seamless"}

CONFLICTS = (
    ("real-time", "batch"),
    ("offline", "always up to date"),
    ("no storage", "audit log"),
    ("stateless", "session"),
    ("no database", "history"),
    ("anonymous", "per-user"),
    ("synchronous", "eventually consistent"),
    ("no pii", "email address"),
    ("single region", "multi-region"),
    ("immutable", "delete"),
)


def _pattern(phrase: str) -> re.Pattern:
    words = r"[\s-]+".join(re.escape(w) for w in re.split(r"[\s-]+", phrase))
    return re.compile(rf"(?<![\w-]){words}(?![\w-])", re.IGNORECASE)


_VAGUE = tuple((CANONICAL.get(w, w), _pattern(w)) for w in VAGUE)


def vague_hits(requirements: Sequence[dict]) -> dict[str, list[str]]:
    """{vague word: [requirement ids using it]}, in the order the words first appear."""
    hits: dict[str, list[str]] = {}
    for req in requirements:
        for word, rx in _VAGUE:
            if rx.search(req["text"]) and req["id"] not in hits.get(word, []):
                hits.setdefault(word, []).append(req["id"])
    return hits


def conflicts(requirements: Sequence[dict]) -> list[tuple[str, str, str, str]]:
    """(phrase a, phrase b, id a, id b) for each conflicting pair stated by two different requirements."""
    found = []
    for a, b in CONFLICTS:
        ids_a = [r["id"] for r in requirements if _pattern(a).search(r["text"])]
        ids_b = [r["id"] for r in requirements if _pattern(b).search(r["text"])]
        pair = next(((x, y) for x in ids_a for y in ids_b if x != y), None)
        if pair:
            found.append((a, b, *pair))
    return found
