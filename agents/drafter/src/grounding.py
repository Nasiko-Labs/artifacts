"""Pure: specific-token grounding. Every number, amount, percentage, date, weekday, month, time, email
address, link, phone number and capitalised name in a draft must appear in what the user gave;
otherwise it is a fact the model made up and becomes a placeholder like [date?]."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

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
    ("date", (rf"\b(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?"
             rf"|(?:{_MONTHS})\.?\s+{_DAY}(?:,?\s+\d{{4}})?|{_DAY}\s+(?:of\s+)?(?:{_MONTHS})\b(?:,?\s+\d{{4}})?"
             rf"|(?:{_WEEKDAYS})\b|(?:{_MONTHS})\b(?:\s+\d{{4}})?|the\s+\d{{1,2}}(?:st|nd|rd|th))")),
    ("time", r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm|a\.m\.|p\.m\.)|\b\d{1,2}:\d{2}\b"),
    ("amount", (rf"(?:[$€£₹]\s?{_NUM}(?:\s?(?:k|m|bn|million|billion|thousand))?\b"
               rf"|\b{_NUM}\s?(?:k|m|bn|million|billion|thousand)?\s?(?:usd|eur|gbp|inr|dollars|euros|pounds|rupees)\b)")),
    ("percent", rf"\b{_NUM}\s?(?:%|percent\b)"),
    ("phone", r"(?<![\w])\+?\d[\d ().-]{7,}\d(?![\w])"),
    ("number", rf"(?<![\w.]){_NUM}(?:st|nd|rd|th)?(?![\w])"),
)
_SPAN_RE = re.compile("|".join(f"(?P<{kind}>{pattern})" for kind, pattern in _KINDS), re.IGNORECASE)
# Capitalised words, case-sensitive; a run of them is one name ("Acme Corp").
_NAME_RE = re.compile(r"\b[A-Z][\w'’&-]*(?:[ \t]+[A-Z][\w'’&-]*)*")
_COMMON = frozenset(["i", "i'm", "i'll", "i've", "i'd", "ok", "okay", "hi", "hello", "hey", "dear", "thanks", "thank", "best", "regards", "cheers", "team", "everyone", "all", "fyi", "asap", "ps", "re", "fwd", "am", "pm", "eod", "eow", "tbd", "sincerely", "kind", "warm", "warmly", "please", "mr", "mrs", "ms", "dr", "sir", "madam", "folks", "subject", "quick", "question", "update", "follow", "request", "hope", "just", "let", "looking", "happy", "sorry", "great", "sounds", "yes", "no"])
_PLACEHOLDER_KIND = {"link": "link", "email": "email", "phone": "phone", "amount": "amount", "percent": "percent",
                     "date": "date", "time": "time", "number": "number", "name": "name"}
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
        if span.kind in ("link", "email", "phone"):
            return _squash(span.text) in _squash(self.text)
        numbers, words = _numbers(span.text.casefold()), _words(span.text.casefold())
        return all(n in self.numbers for n in numbers) and all(w in self.words for w in words)


def _numbers(text: str) -> list[str]:
    return [n.replace(",", "").rstrip(".") for n in _ATOM_NUM.findall(text)]


def _words(text: str) -> list[str]:
    words = (w.replace("’", "'") for w in _ATOM_WORD.findall(text))
    return [w.removesuffix("'s") for w in words if w not in ("st", "nd", "rd", "th", "of", "the")]


def _squash(text: str) -> str:
    return re.sub(r"[\s().-]", "", text.casefold()).rstrip("/.,")


def specific_spans(text: str) -> list[Span]:
    """Every specific token in `text`, outside placeholders, in order."""
    blocked = [(m.start(), m.end()) for m in PLACEHOLDER.finditer(text)]
    spans = [s for s in (_span(text, m) for m in _SPAN_RE.finditer(text)) if s]
    taken = [*blocked, *((s.start, s.end) for s in spans)]
    names = [Span(m.start(), m.end(), "name", m.group(0)) for m in _NAME_RE.finditer(text)
             if not _overlaps(m.start(), m.end(), taken)]
    found = [s for s in spans if not _overlaps(s.start, s.end, blocked)]
    return sorted([*found, *(n for n in (_trim_name(text, n) for n in names) if n)], key=lambda s: s.start)


def _span(text: str, match: re.Match) -> Span | None:
    token = match.group(0).rstrip(".,")
    if match.lastgroup == "date" and token.isalpha() and not _is_date_word(text, match.start(), token):
        return None
    return Span(match.start(), match.start() + len(token), match.lastgroup, token)


def _is_date_word(text: str, pos: int, word: str) -> bool:
    """A bare month or weekday counts only when capitalised, and "May"/"March" not at a sentence start
    (where they are more often a verb)."""
    if not word[0].isupper():
        return False
    return not (word.casefold() in ("may", "march") and _sentence_start(text, pos))


def _trim_name(text: str, span: Span) -> Span | None:
    """Drop a sentence-start first word and common words; what is left is the name to check."""
    words = list(re.finditer(r"\S+", span.text))
    if words and _sentence_start(text, span.start):
        words = words[1:]
    words = [w for w in words if w.group(0).casefold().strip("'’.,") not in _COMMON]
    if not words:
        return None
    start, end = span.start + words[0].start(), span.start + words[-1].end()
    return Span(start, end, "name", text[start:end])


def _sentence_start(text: str, pos: int) -> bool:
    before = text[:pos].rstrip(" \t\"'(*_“‘")
    if not before or before[-1] in ".!?\n:;":
        return True
    line = before.rsplit("\n", 1)[-1].strip()
    return bool(re.fullmatch(r"(?:[-*•]|\d+[.)])", line))


def _overlaps(start: int, end: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start < r_end and r_start < end for r_start, r_end in ranges)


def ungrounded(text: str, ground: Ground, allowed: Ground | None = None) -> list[Span]:
    """Specific tokens the user never gave. `allowed` adds the tone profile's own greeting/sign-off."""
    return [s for s in specific_spans(text or "") if not ground.has(s) and not (allowed and allowed.has(s))]


def replace_spans(text: str, spans: Iterable[Span]) -> str:
    out = text
    for span in sorted(spans, key=lambda s: s.start, reverse=True):
        out = out[:span.start] + span.placeholder + out[span.end:]
    return out


def placeholders(text: str) -> list[str]:
    """The placeholders in a body, recomputed in code (the model's own list is never trusted)."""
    return list(dict.fromkeys(m.group(0) for m in PLACEHOLDER.finditer(text or "")))
