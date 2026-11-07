"""The keyword step: one LLM call turns the topic into search terms (JSON), cleaned in code.

Code removes stop-words, de-duplicates and caps the list at 6. Whether the topic is too vague (fewer
than 2 meaningful words) is decided in code from the user's own words, never by the model. Matching a
text against the terms is code too (case-insensitive, a plural `s` ignored)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import prompts
from verify import parse_object, text_of

MAX_TERMS = 6
MIN_MEANINGFUL = 2
_WORD = re.compile(r"[a-z0-9][a-z0-9_./+-]*", re.IGNORECASE)
STOP_WORDS = frozenset(["a", "about", "above", "after", "again", "all", "also", "am", "an", "and", "any", "anyone", "anybody", "are", "around", "as", "ask", "at", "be", "been", "being", "best", "both", "but", "by", "can", "could", "did", "do", "does", "doing", "done", "expert", "experts", "explain", "familiar", "for", "from", "get", "give", "has", "have", "having", "he", "help", "her", "here", "him", "his", "how", "i", "in", "into", "is", "it", "its", "knew", "know", "knowledge", "knowledgeable", "knows", "knowing", "let", "like", "me", "more", "most", "my", "myself", "need", "of", "on", "one", "only", "or", "our", "ours", "out", "owner", "owners", "owns", "person", "people", "please", "question", "questions", "she", "should", "so", "some", "someone", "somebody", "something", "tell", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "those", "to", "understand", "understands", "understood", "up", "us", "was", "we", "well", "were", "what", "whats", "when", "where", "which", "who", "whom", "whos", "whose", "why", "will", "with", "would", "you", "your", "yours", "exclude", "excluding", "except", "without", "repo", "repository", "channel", "json", "our", "team", "company", "here", "thing", "things", "stuff", "works", "work", "working", "our's", "it's", "what's", "who's"])


@dataclass(frozen=True)
class Keywords:
    terms: tuple[str, ...]
    paths: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"terms": list(self.terms), "paths": list(self.paths)}


@dataclass(frozen=True)
class KeywordAnswer:
    off_topic: bool
    keywords: Keywords | None = None


def meaningful_words(text: str) -> tuple[str, ...]:
    """The user's words minus stop-words, channel and repo hints, in order, de-duplicated."""
    stripped = re.sub(r"#[\w.-]+", " ", text or "")
    words = (w.strip("./+-").casefold() for w in _WORD.findall(stripped))
    return _dedupe(w for w in words if w and len(w) > 1 and w not in STOP_WORDS and not w.isdigit())


def is_vague(topic: str) -> bool:
    return len(meaningful_words(topic)) < MIN_MEANINGFUL


def parse_answer(text: str, topic: str) -> KeywordAnswer:
    """The model's JSON → cleaned keywords. Unreadable or empty → the user's own meaningful words."""
    stripped = (text or "").strip()
    if stripped.startswith(prompts.OFF_TOPIC):
        return KeywordAnswer(off_topic=True)
    data = parse_object(stripped)
    if data is not None and str(data.get("status") or "").casefold() == "off_topic":
        return KeywordAnswer(off_topic=True)
    raw_terms = [*_strings(data, "terms"), *_strings(data, "synonyms")] if data else []
    terms = clean_terms(raw_terms) or clean_terms(meaningful_words(topic))
    paths = tuple(p for p in _strings(data, "paths_hint") if "/" in p or "." in p)[:3] if data else ()
    return KeywordAnswer(off_topic=False, keywords=Keywords(terms, paths))


def clean_terms(raw: Iterable[str]) -> tuple[str, ...]:
    """Stop-words out (a multi-word term keeps its meaningful words), de-duplicated, at most 6."""
    kept: list[str] = []
    for term in raw:
        words = [w for w in term.casefold().split() if w not in STOP_WORDS]
        phrase = " ".join(words).strip(" .,;:!?\"'")
        if phrase and not phrase.isdigit() and len(phrase) > 1:
            kept.append(phrase)
    return _dedupe(kept)[:MAX_TERMS]


def hits(text: str, terms: Sequence[str]) -> int:
    """How many distinct terms the text contains (case-insensitive; "retries" matches "retry")."""
    lowered = (text or "").casefold()
    return sum(1 for term in terms if _contains(lowered, term))


def _contains(lowered: str, term: str) -> bool:
    stem = term[:-1] if term.endswith("s") and len(term) > 3 else term
    stem = stem[:-1] if stem.endswith("y") and len(stem) > 3 else stem
    return stem in lowered


def _strings(data: dict | None, key: str) -> list[str]:
    values = data.get(key) if isinstance(data, dict) else None
    return [text_of(v) for v in values if text_of(v)] if isinstance(values, list) else []


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))
