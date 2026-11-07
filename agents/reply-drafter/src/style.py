"""Pure: style metrics measured in code from the user's past replies. The metrics are the truth the reply
is checked against; the model sees only the plain-word descriptors built from them, never the examples."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

import textstats as ts

FORMALITY = {1: "very casual", 2: "casual", 3: "neutral", 4: "formal", 5: "very formal"}
NONE = "none"
DEFAULT_GREETING = "Hi {first},"
DEFAULT_SIGN_OFF = "Thanks,\n[your name?]"

_GREETING = re.compile(r"^(?P<word>hi|hey|hello|hiya|dear|morning|good morning|yo|heya)\b"
                       r"(?:[ \t]+(?P<who>[A-Za-z][\w'’-]*))?[ \t]*(?P<punct>[,!]|(?=\n)|$)", re.IGNORECASE)
_GROUP_WORDS = frozenset(["all", "team", "folks", "everyone", "there", "guys", "y'all", "both"])
_CLOSER = re.compile(r"(?:thanks|thank you|many thanks|cheers|best|best regards|kind regards|regards|sincerely|"
                     r"talk soon|take care|ta|thx|warmly|speak soon)[,!.]?", re.IGNORECASE)
_INLINE_SIGN_OFF = re.compile(rf"(?:^|(?<=[.!?])\s+)({_CLOSER.pattern}(?:[ \t]+[A-Z][\w.]*)?\.?)\s*$", re.IGNORECASE)
_FORMAL_MARKS = re.compile(r"\bdear\b|\bbest regards\b|\bsincerely\b|\bkind regards\b", re.IGNORECASE)
_NEUTRAL_MARKS = re.compile(r"\bhi\b|\bthanks\b", re.IGNORECASE)
_CASUAL_MARKS = re.compile(r"\bhey\b|\bcheers\b", re.IGNORECASE)
MAX_SIGN_OFF_WORDS = 4


@dataclass(frozen=True)
class Metrics:
    examples: int = 0                  # 0: the neutral default, nothing measured
    avg_words: int = 0
    avg_sentence_words: int = 0
    contraction_rate: float = 0.0      # per 100 words
    exclaim_rate: float = 0.0          # per 100 words
    question_rate: float = 0.0         # per 100 words
    emoji: bool = False
    bullets: bool = False
    greeting: str = DEFAULT_GREETING   # with {first} for the recipient's first name, or "none"
    sign_off: str = DEFAULT_SIGN_OFF   # a line break as \n, or "none"
    lowercase_start: bool = False
    formality: int = 3
    top_openers: tuple[str, ...] = ()
    avoids_em_dash: bool = False

    @property
    def measured(self) -> bool:
        return self.examples > 0

    def to_dict(self) -> dict:
        return {**asdict(self), "top_openers": list(self.top_openers)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Metrics:
        if not data:
            return Metrics()
        known = {k: data[k] for k in Metrics.__dataclass_fields__ if k in data}
        known["top_openers"] = tuple(known.get("top_openers") or ())
        for key in ("examples", "avg_words", "avg_sentence_words", "formality"):
            known[key] = int(known.get(key) or 0)
        known["formality"] = known["formality"] or 3
        return Metrics(**known)

    def with_sign_off(self, name: str) -> Metrics:
        """'sign off as A.': keep the measured closer word, use the given name."""
        if not name:
            return self
        closer = self.sign_off.split("\n")[0].split(" ")[0] if self.sign_off != NONE else "Thanks,"
        closer = closer if closer.endswith(",") else closer.rstrip(".!") + ","
        return replace(self, sign_off=f"{closer} {name.strip()}")


def measure(examples: Sequence[str]) -> Metrics:
    texts = [e.strip() for e in examples if e and e.strip()]
    if not texts:
        return Metrics()
    words = [ts.word_count(t) for t in texts]
    total = max(1, sum(words))
    contractions = sum(ts.contractions(t) for t in texts)
    exclaims = sum(t.count("!") for t in texts)
    greeting = _most_common([greeting_pattern(t) for t in texts])
    sign_off = _most_common([sign_off_of(t) for t in texts])
    metrics = Metrics(
        examples=len(texts), avg_words=round(total / len(texts)),
        avg_sentence_words=round(total / max(1, sum(ts.sentence_count(t) for t in texts))),
        contraction_rate=round(100 * contractions / total, 1), exclaim_rate=round(100 * exclaims / total, 1),
        question_rate=round(100 * sum(t.count("?") for t in texts) / total, 1),
        emoji=any(ts.has_emoji(t) for t in texts), bullets=any(ts.has_bullets(t) for t in texts),
        greeting=greeting, sign_off=sign_off, lowercase_start=all(t[0].islower() for t in texts),
        top_openers=tuple(" ".join(t.split()[:2]) for t in texts), avoids_em_dash=not any("—" in t for t in texts))
    return replace(metrics, formality=formality(texts, metrics))


def formality(texts: Sequence[str], m: Metrics) -> int:
    """1-5 from rules, in order (approximate): many contractions with ! or emoji -> 2; no contractions and
    a formal greeting/closer -> 5; Hi/Thanks -> 3; Hey/cheers -> 2; else 3."""
    joined = "\n".join(texts)
    if m.contraction_rate > 3 and (m.exclaim_rate > 0 or m.emoji):
        return 2
    if m.contraction_rate == 0 and _FORMAL_MARKS.search(joined):
        return 5
    if _NEUTRAL_MARKS.search(joined):
        return 3
    if _CASUAL_MARKS.search(joined):
        return 2
    return 3


def greeting_pattern(text: str) -> str:
    match = _GREETING.match(text.strip())
    if match is None:
        return NONE
    word, who, punct = match.group("word"), match.group("who") or "", match.group("punct") or ""
    if who and who.casefold() in _GROUP_WORDS:
        return f"{word} {who}{punct}"
    if who:
        return f"{word} {{first}}{punct}"
    return f"{word}{punct}"


def sign_off_of(text: str) -> str:
    """The closing line(s): the last line (or the last two, closer then name) when short, or a closer at
    the end of a one-line message ("... works. Cheers, A")."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) >= 2:
        last, before = lines[-1], lines[-2]
        if len(before.split()) <= MAX_SIGN_OFF_WORDS and _CLOSER.fullmatch(before) and len(last.split()) <= 2:
            return f"{before}\n{last}"
        if len(last.split()) <= MAX_SIGN_OFF_WORDS and _CLOSER.match(last):
            return last
    inline = _INLINE_SIGN_OFF.search(lines[-1] if lines else "")
    if inline and len(inline.group(1).split()) <= MAX_SIGN_OFF_WORDS:
        return inline.group(1).strip()
    return NONE


def _most_common(values: Sequence[str]) -> str:
    counts = Counter(values)
    best = max(counts.values())
    return next(v for v in values if counts[v] == best)


def descriptors(m: Metrics, short: bool = False) -> str:
    """The style in plain words and numbers, for the model."""
    if not m.measured:
        lines = ["formality: 3/5 (neutral)", f"greeting: {DEFAULT_GREETING}",
                 f"sign-off: {DEFAULT_SIGN_OFF.replace(chr(10), ' / ')}", "no emoji"]
        return "\n".join(f"- {line}" for line in lines)
    low, high = length_window(m, short)
    lines = [f"formality: {m.formality}/5 ({FORMALITY[m.formality]}, approx.)",
             f"length: about {m.avg_words} words (between {low} and {high})",
             f"about {m.avg_sentence_words} words per sentence",
             f"greeting: {m.greeting if m.greeting != NONE else 'none, start straight in'}",
             f"sign-off: {m.sign_off.replace(chr(10), ' / ') if m.sign_off != NONE else 'none'}",
             _contraction_words(m), "exclamation marks: " + ("none" if not m.exclaim_rate
                                                             else f"about {m.exclaim_rate:g} per 100 words"),
             "emoji: " + ("yes, sometimes" if m.emoji else "never"),
             "bullet points: " + ("fine" if m.bullets else "never")]
    if m.lowercase_start:
        lines.append("starts with a lowercase letter")
    if m.avoids_em_dash:
        lines.append("never uses em dashes")
    return "\n".join(f"- {line}" for line in lines)


def _contraction_words(m: Metrics) -> str:
    if m.contraction_rate == 0:
        return "contractions: none (write 'do not', not 'don't')"
    if m.contraction_rate >= 2:
        return f"contractions: yes (about 1 per {max(1, round(100 / m.contraction_rate))} words)"
    return "contractions: occasionally"


def length_window(m: Metrics, short: bool = False) -> tuple[int, int]:
    low, high = ts.length_bounds(m.avg_words)
    return (0 if short else low), high
