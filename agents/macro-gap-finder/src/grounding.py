"""Pure: a draft macro may only state what at least two of its member replies state.

Checked in the draft: numbers, amounts, time frames ("within 3 business days"), links, quoted UI labels,
capitalised names/phrases (not sentence-initial) and promise words (guarantee, always, never, refund). Each must
appear in >= MIN_SUPPORT member replies. Placeholders ({{customer_name}} ...) are always allowed."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

import scrub
from lexicon import content_tokens

MIN_SUPPORT = 2
MAX_DRAFT_WORDS = 120
MAX_PATTERN_WORDS = 10
PATTERN_OVERLAP = 0.6
PROMISE_WORDS = ("guarantee", "guaranteed", "always", "never", "refund", "refunds", "refunded")

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_WORD_NUMBER = r"(?:\d+(?:[.,]\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fourteen|thirty|a few)"
_TIME = re.compile(rf"\b{_WORD_NUMBER}(?:\s*-\s*{_WORD_NUMBER})?\s+(?:business\s+|working\s+|calendar\s+)?"
                   r"(?:minutes?|hours?|days?|weeks?|months?|years?)\b|\b24/7\b", re.IGNORECASE)
_AMOUNT = re.compile(r"[$€£]\s?\d[\d,.]*|\b\d[\d,.]*\s?(?:usd|eur|gbp|dollars|euros|pounds)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,:]\d+)*%?")
_URL = re.compile(r"(?:https?://|www\.)\S*[^\s.,;:!?)\]]")
_QUOTED = re.compile(r"[\"“]([^\"”\n]{2,40})[\"”]")
_CAPS = re.compile(r"(?<![.!?]\s)(?<!^)(?<!\n)\b([A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*)*)")
_MONEY_OR_TIME = re.compile(r"[$€£]|\brefund|\bcharge|\bprice|\bfee\b|\bcredit\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CLAUSE = re.compile(r"[.,;:!?\n]+")
_COMMON_CAPS = {"i", "you", "we", "our", "your", "the", "please", "thanks", "thank", "hi", "hello", "if", "once",
                "then", "this", "that", "it", "let", "best", "kind"}


def _plain(text: str) -> str:
    return " ".join(_PLACEHOLDER.sub(" ", text or "").casefold().split())


def facts(draft: str) -> list[str]:
    """The checkable items in a draft, in order, unique (casefolded)."""
    text = _PLACEHOLDER.sub(" ", draft or "")
    found: list[str] = []
    found += [m.group(0) for m in _TIME.finditer(text)]
    found += [m.group(0) for m in _AMOUNT.finditer(text)]
    found += [m.group(0) for m in _URL.finditer(text)]
    found += [m.group(1) for m in _QUOTED.finditer(text)]
    found += [m.group(0) for m in _NUMBER.finditer(_URL.sub(" ", text))]
    found += [m.group(1) for m in _CAPS.finditer(text) if m.group(1).casefold() not in _COMMON_CAPS]
    found += [w for w in PROMISE_WORDS if re.search(rf"\b{w}\b", text, re.IGNORECASE)]
    return list(dict.fromkeys(" ".join(f.casefold().split()) for f in found if f.strip()))


def support(item: str, members: Sequence[str]) -> int:
    pattern = re.compile(rf"(?<!\w){re.escape(item)}(?!\w)")
    return sum(1 for m in members if pattern.search(_plain(m)))


def ungrounded(draft: str, members: Sequence[str]) -> list[str]:
    return [item for item in facts(draft) if support(item, members) < MIN_SUPPORT]


def finalize(draft: str, names: Iterable[str]) -> tuple[str, bool]:
    """(the draft re-scrubbed, placeholders normalised, cut at a sentence boundary to 120 words; was it cut)."""
    text = _PLACEHOLDER.sub(lambda m: "{{" + m.group(1) + "}}", scrub.scrub_names(draft.strip(), names))
    if len(text.split()) <= MAX_DRAFT_WORDS:
        return text, False
    kept: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        if len(" ".join([*kept, sentence]).split()) > MAX_DRAFT_WORDS:
            break
        kept.append(sentence)
    return (" ".join(kept) if kept else " ".join(text.split()[:MAX_DRAFT_WORDS]) + " ..."), True


def pattern_ok(pattern: str, members: Sequence[str]) -> bool:
    words = (pattern or "").split()
    tokens = content_tokens(pattern)
    if not words or len(words) > MAX_PATTERN_WORDS or not tokens:
        return False
    vocabulary = {t for m in members for t in content_tokens(m)}
    return sum(1 for t in tokens if t in vocabulary) / len(tokens) >= PATTERN_OVERLAP


def fallback_pattern(medoid_text: str) -> str:
    for clause in _CLAUSE.split(medoid_text or ""):
        words = _PLACEHOLDER.sub("", clause).split()
        if len(content_tokens(" ".join(words))) >= 2:
            return " ".join(words[:MAX_PATTERN_WORDS])
    return " ".join((medoid_text or "").split()[:MAX_PATTERN_WORDS]) or "Recurring answer"


def mentions_money_or_time(draft: str) -> bool:
    return bool(_TIME.search(draft or "") or _AMOUNT.search(draft or "") or _MONEY_OR_TIME.search(draft or ""))
