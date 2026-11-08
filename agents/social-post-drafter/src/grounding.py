"""Pure: specific-token grounding. Every number, percentage, amount, multiplier, date, time, link, email
address, @handle and capitalised name in a post must appear in what the user gave in this conversation;
otherwise the model made it up and it becomes a visible placeholder like [metric?]."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from words import COMMON

PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,30}\?\]")

_MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
           r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_WEEKDAYS = r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:rs(?:day)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?"
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_NUM = r"\d[\d,]*(?:\.\d+)?"
# Order matters: earlier kinds claim their text first.
_KINDS = (
    ("link", r"\b(?:https?://|www\.)[^\s<>()\]]+"),
    ("email", r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    ("handle", r"(?<![\w.])@[A-Za-z0-9_]{1,30}"),
    ("date", (rf"\b(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}"
             rf"|(?:{_MONTHS})\.?\s+{_DAY}(?:,?\s+\d{{4}})?|{_DAY}\s+(?:of\s+)?(?:{_MONTHS})\b(?:,?\s+\d{{4}})?"
             rf"|(?:{_WEEKDAYS})\b|(?:{_MONTHS})\b(?:\s+\d{{4}})?)")),
    ("time", r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|\b\d{1,2}:\d{2}\b"),
    ("amount", (rf"(?:[$€£₹]\s?{_NUM}(?:\s?(?:k|m|bn|million|billion|thousand))?\b"
               rf"|\b{_NUM}\s?(?:k|m|bn|million|billion|thousand)?\s?(?:usd|eur|gbp|inr|dollars|euros|pounds)\b)")),
    ("percent", rf"\b{_NUM}\s?(?:%|percent\b)"),
    ("multiplier", rf"\b{_NUM}\s?x\b"),
    ("number", rf"(?<![\w.]){_NUM}(?:st|nd|rd|th|k|m)?(?![\w])"),
)
_SPAN_RE = re.compile("|".join(f"(?P<{kind}>{pattern})" for kind, pattern in _KINDS), re.IGNORECASE)
_NAME_RE = re.compile(r"\b[A-Z][\w'’&.-]*(?:[ \t]+[A-Z][\w'’&.-]*)*")
_PLACEHOLDER_KIND = {"link": "link", "email": "email", "handle": "name", "amount": "metric", "percent": "metric",
                     "multiplier": "metric", "number": "metric", "date": "date", "time": "date", "name": "name"}
_LIST_MARKER = re.compile(r"(?:^|\n)[ \t]*$")
_STRUCTURAL = re.compile(r"\s+(?:steps|tips|ways|lessons|mistakes|things|reasons|questions|ideas|takeaways)\b",
                         re.IGNORECASE)
_ATOM_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ATOM_WORD = re.compile(r"[a-z][a-z'’]*")


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    text: str

    @property
    def placeholder(self) -> str:
        return f"[{_PLACEHOLDER_KIND[self.kind]}?]"


@dataclass(frozen=True)
class Ground:
    """What the user gave, normalised for lookups."""

    text: str
    numbers: frozenset[str]
    words: frozenset[str]

    @staticmethod
    def of(texts: Iterable[str]) -> Ground:
        joined = "\n".join(t for t in texts if t).casefold()
        return Ground(text=re.sub(r"\s+", " ", joined), numbers=frozenset(_numbers(joined)),
                      words=frozenset(_words(joined)))

    def has(self, span: Span) -> bool:
        if span.kind in ("link", "email", "handle"):
            return _squash(span.text) in _squash(self.text)
        if span.kind == "percent":  # "40%" needs a percentage in the material, not just the number 40
            return any(re.search(rf"(?<![\d.]){re.escape(n)}\s?(?:%|percent)", self.text.replace(",", ""))
                       for n in _numbers(span.text))
        numbers, words = _numbers(span.text.casefold()), _words(span.text.casefold())
        return all(n in self.numbers for n in numbers) and all(w in self.words for w in words)

    def has_word(self, word: str) -> bool:
        return word.casefold() in self.words or word.casefold() in self.text

    def has_quote(self, quote: str) -> bool:
        """A `from` fragment really is the user's: verbatim (whitespace and punctuation aside)."""
        wanted = _letters(quote)
        return bool(wanted) and wanted in _letters(self.text)


def _numbers(text: str) -> list[str]:
    return [n.replace(",", "").rstrip(".") for n in _ATOM_NUM.findall(text)]


def _words(text: str) -> list[str]:
    words = (w.replace("’", "'") for w in _ATOM_WORD.findall(text))
    return [w.removesuffix("'s") for w in words if w not in ("st", "nd", "rd", "th", "of", "x", "k", "m")]


def _squash(text: str) -> str:
    return re.sub(r"[\s().-]", "", text.casefold()).rstrip("/.,")


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).strip()


def specific_spans(text: str) -> list[Span]:
    """Every specific token in `text`, outside placeholders, in order."""
    blocked = [(m.start(), m.end()) for m in PLACEHOLDER.finditer(text)]
    spans = [s for s in (_span(text, m) for m in _SPAN_RE.finditer(text)) if s]
    taken = [*blocked, *((s.start, s.end) for s in spans)]
    names = [Span(m.start(), m.end(), "name", m.group(0).rstrip(".")) for m in _NAME_RE.finditer(text)
             if not _overlaps(m.start(), m.end(), taken)]
    found = [s for s in spans if not _overlaps(s.start, s.end, blocked)]
    return sorted([*found, *(n for n in (_trim_name(text, n) for n in names) if n)], key=lambda s: s.start)


def _span(text: str, match: re.Match) -> Span | None:
    token = match.group(0).rstrip(".,")
    kind = match.lastgroup
    if kind == "date" and token.isalpha() and not _is_date_word(text, match.start(), token):
        return None
    if kind == "number" and _structural(text, match.start(), match.start() + len(token)):
        return None
    return Span(match.start(), match.start() + len(token), kind, token)


def _structural(text: str, start: int, end: int) -> bool:
    """A list marker ("1. ") or a small listicle count ("3 tips") is structure, not a fact."""
    if _LIST_MARKER.search(text[:start]) and text[end:end + 1] in (".", ")"):
        return True
    return text[start:end].isdigit() and int(text[start:end]) <= 10 and bool(_STRUCTURAL.match(text, end))


def _is_date_word(text: str, pos: int, word: str) -> bool:
    if not word[0].isupper():
        return False
    return not (word.casefold() in ("may", "march") and _sentence_start(text, pos))


def _trim_name(text: str, span: Span) -> Span | None:
    words = list(re.finditer(r"\S+", span.text))
    first = words[0].group(0).casefold().strip("'’.,") if words else ""
    if words and _sentence_start(text, span.start) and (len(words) == 1 or first in COMMON):
        words = words[1:]  # a lone capitalised word at a sentence start is usually not a name
    words = [w for w in words if w.group(0).casefold().strip("'’.,") not in COMMON]
    if not words:
        return None
    start, end = span.start + words[0].start(), span.start + words[-1].end()
    return Span(start, end, "name", text[start:end])


def _sentence_start(text: str, pos: int) -> bool:
    before = text[:pos].rstrip(" \t\"'(*_“‘")
    if not before or before[-1] in ".!?\n:;—–-":
        return True
    line = before.rsplit("\n", 1)[-1].strip()
    return bool(re.fullmatch(r"(?:[-*•→]|\d+[.)])", line))


def _overlaps(start: int, end: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start < r_end and r_start < end for r_start, r_end in ranges)


def ungrounded(text: str, ground: Ground) -> list[Span]:
    return [s for s in specific_spans(text or "") if not ground.has(s)]


def replace_spans(text: str, spans: Iterable[Span]) -> str:
    out = text
    for span in sorted(spans, key=lambda s: s.start, reverse=True):
        out = out[:span.start] + span.placeholder + out[span.end:]
    return out


def placeholders(text: str) -> list[str]:
    """The placeholders in a body, recomputed in code (the model's own list is never trusted)."""
    return list(dict.fromkeys(m.group(0) for m in PLACEHOLDER.finditer(text or "")))
