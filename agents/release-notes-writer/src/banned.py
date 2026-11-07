"""Pure: the word lists code enforces on every model sentence.

BANNED marketing adjectives trigger one repair, then are removed. JARGON words are flagged (and trigger
the repair) unless the audience is developers. URGENCY words never appear in the Slack or email text."""

from __future__ import annotations

import re

BANNED = ("powerful", "seamless", "seamlessly", "revolutionary", "game-changing", "game changing", "blazing",
          "blazing-fast", "amazing", "cutting-edge", "best-in-class", "world-class", "groundbreaking", "magical")
JARGON = ("refactor", "refactored", "lint", "linting", "endpoint", "endpoints", "PR", "PRs", "merge", "merged",
          "lambda", "k8s", "kubernetes", "migration", "migrations", "regex", "null pointer", "stack trace")
URGENCY = ("urgent", "act now", "immediately", "hurry", "don't miss", "do not miss", "last chance", "asap")
FUTURE = ("coming soon", "in a future release", "upcoming", "next release", "we plan to", "we are working on",
          "we're working on", "stay tuned", "roadmap")
EXPLOIT = ("exploit", "payload", "attacker could", "proof of concept", "proof-of-concept")


def _pattern(words: tuple[str, ...], case_sensitive: frozenset[str] = frozenset()) -> re.Pattern:
    parts = [rf"\b{re.escape(w)}\b" if w not in case_sensitive else rf"(?-i:\b{re.escape(w)}\b)" for w in words]
    return re.compile("|".join(sorted(parts, key=len, reverse=True)), re.IGNORECASE)


_BANNED = _pattern(BANNED)
_JARGON = _pattern(JARGON, frozenset({"PR", "PRs"}))
_URGENCY = _pattern(URGENCY)
_FUTURE = _pattern(FUTURE)
_EXPLOIT = _pattern(EXPLOIT)


def find_banned(text: str) -> list[str]:
    return [m.group(0) for m in _BANNED.finditer(text or "")]


def remove_banned(text: str) -> str:
    out = _BANNED.sub("", text or "")
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return " ".join(out.split())


def find_jargon(text: str) -> list[str]:
    return [m.group(0) for m in _JARGON.finditer(text or "")]


def has_urgency(text: str) -> bool:
    return bool(_URGENCY.search(text or ""))


def has_future(text: str) -> bool:
    return bool(_FUTURE.search(text or ""))


def has_exploit_detail(text: str) -> bool:
    return bool(_EXPLOIT.search(text or ""))
