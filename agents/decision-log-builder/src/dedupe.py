"""Pure: decisions already in the log. A decision is a duplicate when its normalised words overlap an
existing log line (or one `|` segment of it) with Jaccard >= 0.6, or its link already appears in the log.
Duplicates are marked `duplicate_of` and left out of the default write set."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from models import Decision

JACCARD_THRESHOLD = 0.6
MIN_LINE_WORDS = 2
_WORD = re.compile(r"[a-z0-9]+")
STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "to", "of", "for", "in", "on", "at", "by", "with", "from", "as", "is",
    "are", "was", "were", "be", "been", "we", "our", "us", "it", "its", "this", "that", "these", "those", "will",
    "would", "should", "can", "could", "use", "using", "go", "going", "decided", "decision", "agreed", "let",
    "lets", "s", "ll", "re", "i", "you", "they", "all", "so", "then", "into", "per", "via",
})


def words(text: str) -> frozenset[str]:
    return frozenset(w for w in _WORD.findall((text or "").casefold()) if w not in STOP_WORDS)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def log_lines(text: str) -> tuple[str, ...]:
    """Each non-empty line of the log, plus each `|` segment of a line (our own entry format)."""
    lines = [" ".join(line.split()).lstrip("-*> ").strip() for line in (text or "").splitlines()]
    parts = [p.strip() for line in lines for p in (line, *line.split("|")) if p.strip()]
    return tuple(dict.fromkeys(p for p in parts if len(words(p)) >= MIN_LINE_WORDS))


def mark(decisions: Sequence[Decision], log_text: str) -> tuple[Decision, ...]:
    lines = log_lines(log_text)
    lowered = (log_text or "").casefold()
    return tuple(replace(d, duplicate_of=_match(d, lines, lowered)) for d in decisions)


def _match(decision: Decision, lines: Sequence[str], lowered: str) -> str | None:
    if decision.link and decision.link.casefold() in lowered:
        return "the same link is already in the log"
    own = words(decision.decision)
    best = max(lines, key=lambda line: jaccard(own, words(line)), default=None)
    if best is None or jaccard(own, words(best)) < JACCARD_THRESHOLD:
        return None
    return best if len(best) <= 140 else best[:139] + "…"
