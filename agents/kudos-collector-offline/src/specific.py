"""Pure: is a praise message specific enough to become an example? Decided in code; the model's own `specific`
value is ignored.

Specific = it names concrete work: a PR/ticket reference, a feature or PR-title phrase from this paste, or an
action verb with an object ("fixed the export bug", "for staying late to help ship the migration"). A message
that reduces to thanks and superlatives once names are removed ("great job!", "you rock") is counted, never
turned into an example."""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable

from identity import normalise

UNSPECIFIC = (
    "great job", "nice work", "nice job", "good job", "well done", "awesome", "amazing", "thanks", "thank you",
    "thx", "you rock", "kudos", "legend", "thank you so much", "thanks so much", "huge thanks", "props", "great work",
    "great", "nice", "fantastic", "incredible", "brilliant", "shoutout", "shout out", "appreciate it", "rockstar",
    "superstar", "hero", "mvp", "as always", "again", "everyone", "team", "all", "guys", "folks", "y'all", "so much",
    "really", "very", "for", "to", "the", "a", "you", "your", "and", "much", "big", "to you", "this week",
    "today", "yesterday", "work", "job", "everything", "help", "all the help", "being", "awesome work",
)
_UNSPECIFIC_WORDS = frozenset(w for phrase in UNSPECIFIC for w in phrase.split())
REFERENCE = re.compile(r"(?:^|\s|\()#\d+\b|\b[A-Z]{2,10}-\d+\b|/pull/\d+")
IRREGULAR = frozenset({"built", "wrote", "ran", "made", "led", "took", "caught", "found", "drove", "shipped", "fixed",
                       "brought", "sent", "taught", "kept", "held", "gave", "did", "spent", "stood", "set", "got",
                       "put", "cut", "rewrote", "split", "won"})
ADJECTIVE_ED = frozenset({"talented", "dedicated", "detailed", "focused", "skilled", "excited", "motivated", "need"})
STOP = frozenset({"the", "a", "an", "to", "of", "for", "and", "in", "on", "with", "at", "by", "our", "my", "us",
                  "this", "that", "it", "is", "be", "from", "add", "fix", "new", "v2", "api"})
_WORD = re.compile(r"[a-z][a-z'-]*")


def _action_with_object(words: list[str]) -> bool:
    for i, w in enumerate(words[:-1]):
        nxt = words[i + 1]
        past = (w.endswith("ed") and len(w) > 4 and w not in ADJECTIVE_ED) or w in IRREGULAR
        gerund = w.endswith("ing") and len(w) > 4 and i > 0 and words[i - 1] in ("for", "on", "with")
        if (past or gerund) and nxt not in _UNSPECIFIC_WORDS - {"the", "a", "your"}:
            return True
    return False


def work_phrases(titles: Iterable[str]) -> set[str]:
    """Distinctive 2-word phrases from PR titles and feature names in this paste."""
    out: set[str] = set()
    for title in titles:
        words = [w for w in _WORD.findall(title.casefold()) if w not in STOP]
        out |= {f"{a} {b}" for a, b in itertools.pairwise(words)}
    return out


def is_specific(message: str, names: Iterable[str] = (), phrases: Iterable[str] = ()) -> bool:
    text = message or ""
    if REFERENCE.search(text):
        return True
    plain = f" {normalise(re.sub(r'@[\w.-]+', ' ', text))} "
    for name in names:
        norm = normalise(name)
        if norm:
            plain = plain.replace(f" {norm} ", " ")
            for token in norm.split():
                plain = plain.replace(f" {token} ", " ")
    words = _WORD.findall(plain)
    if all(w in _UNSPECIFIC_WORDS for w in words):
        return False
    content = [w for w in words if w not in STOP]
    joined = " ".join(content)
    if any(p in joined for p in phrases):
        return True
    return _action_with_object(words)
