"""Pure: find AI-writing patterns in a text and score their density (hits per 100 words).

Detection runs on the masked text, so direct quotes, code and links never count. Overlapping matches count
once (the longer span wins, then the category listed first). Em dashes count, one per dash, only when the
text uses more than one per 150 words (the category is overuse, not the dash)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import patterns_phrases as lex
from mask import mask

CLEAN_DENSITY = 1.0
_FLAGS = re.IGNORECASE | re.MULTILINE
_COMPILED = tuple((cat, re.compile(p, _FLAGS)) for cat, ps in lex.PHRASES.items() for p in ps)
_UPBEAT = tuple(re.compile(p, re.IGNORECASE) for p in lex.UPBEAT_ENDINGS)
_EM_DASH = re.compile(lex.EM_DASH)
_TRIPLE = re.compile(r"\b([\w-]+), ([\w-]+),? and ([\w-]+)\b", re.IGNORECASE)
_EMOJI_BULLET = re.compile(r"^[ \t]*[\U0001F300-\U0001FAFF☀-➿]", re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+)$", re.MULTILINE)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_SMALL_WORDS = frozenset({"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to"})
_PRIORITY = {cat: i for i, cat in enumerate(lex.CATEGORIES)}


@dataclass(frozen=True)
class Hit:
    category: str
    phrase: str
    start: int
    end: int


@dataclass(frozen=True)
class Detection:
    words: int
    hits: tuple[Hit, ...]
    em_dashes: int
    em_dash_overused: bool

    @property
    def density(self) -> float:
        return round(len(self.hits) / self.words * 100, 1) if self.words else 0.0

    def counts(self) -> dict[str, int]:
        return dict(Counter(h.category for h in self.hits))

    @property
    def has_artefact(self) -> bool:
        return any(h.category in lex.ARTEFACT_CATEGORIES for h in self.hits)

    @property
    def is_clean(self) -> bool:
        return self.density < CLEAN_DENSITY and not self.has_artefact and not self.em_dash_overused

    def phrases(self, category: str) -> list[str]:
        return list(dict.fromkeys(h.phrase for h in self.hits if h.category == category))


def word_count(text: str) -> int:
    return len((text or "").split())


def em_dash_budget(words: int) -> int:
    return max(1, words // lex.EM_DASH_WORDS_PER_DASH)


def detect(text: str) -> Detection:
    words = word_count(text)
    masked = mask(text).text
    candidates = [*_phrase_hits(masked), *_triples(masked), *_typography(masked), *_upbeat(masked)]
    dashes = [Hit("em_dash", m.group(0), m.start(), m.end()) for m in _EM_DASH.finditer(masked)]
    overused = len(dashes) > em_dash_budget(words)
    hits = _non_overlapping(candidates) + (dashes if overused else [])
    return Detection(words, tuple(sorted(hits, key=lambda h: h.start)), len(dashes), overused)


def _phrase_hits(text: str) -> list[Hit]:
    return [Hit(cat, " ".join(m.group(0).split()).strip(" ,"), m.start(), m.end())
            for cat, pattern in _COMPILED for m in pattern.finditer(text) if m.group(0).strip()]


def _triples(text: str) -> list[Hit]:
    return [Hit("rule_of_three", m.group(0), m.start(), m.end()) for m in _TRIPLE.finditer(text)
            if all(w.casefold() in lex.POWER_WORDS for w in m.groups())]


def _typography(text: str) -> list[Hit]:
    hits = [Hit("typography", m.group(0).strip(), m.start(), m.end()) for m in _EMOJI_BULLET.finditer(text)]
    for m in _HEADING.finditer(text):
        major = [w for w in m.group(1).split() if w.casefold() not in _SMALL_WORDS]
        if len(major) >= 3 and all(w[:1].isupper() for w in major):
            hits.append(Hit("typography", m.group(0).strip(), m.start(), m.end()))
    curly = re.search(r"[“”]", text)
    if curly and '"' in text:  # curly and straight double quotes both used (apostrophes don't count)
        hits.append(Hit("typography", "mixed curly and straight quotes", curly.start(), curly.end()))
    return hits


def _upbeat(text: str) -> list[Hit]:
    stripped = text.rstrip()
    ends = [m.end() for m in _SENTENCE_END.finditer(stripped)]
    start = ends[-2] if len(ends) >= 2 else 0
    last = stripped[start:]
    return [Hit("upbeat_conclusion", m.group(0), start + m.start(), start + m.end())
            for p in _UPBEAT for m in p.finditer(last)]


def _non_overlapping(candidates: list[Hit]) -> list[Hit]:
    ordered = sorted(candidates, key=lambda h: (-(h.end - h.start), _PRIORITY[h.category], h.start))
    kept: list[Hit] = []
    for hit in ordered:
        if all(hit.end <= k.start or hit.start >= k.end for k in kept):
            kept.append(hit)
    return kept
