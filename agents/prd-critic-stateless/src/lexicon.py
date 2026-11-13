"""Pure: the vague-term lexicon and the number test. A hit counts only in a line with no number, so
"faster by 30%" is specific and "faster" is not."""

from __future__ import annotations

import re

VAGUE = ("fast", "quick", "easy", "simple", "intuitive", "seamless", "robust", "scalable", "user-friendly",
         "flexible", "modern", "best-in-class", "world-class", "as needed", "appropriate", "etc", "and so on",
         "where possible", "if time permits", "reasonable", "significant", "improve", "better", "optimize")
LENIENT_DROPS = frozenset({"significant", "improve", "better", "optimize", "flexible", "modern"})
_SUFFIX = r"(?:s|d|r|er|est|ly|ed|ing|ness|ment|ments|ally|ally)?"
_NUMBER = re.compile(r"\d")


def terms(lenient: bool = False) -> tuple[str, ...]:
    return tuple(t for t in VAGUE if not (lenient and t in LENIENT_DROPS))


def _pattern(term: str) -> re.Pattern[str]:
    stem = term[:-1] if term.endswith("e") and " " not in term else term
    body = re.escape(stem).replace(r"\ ", r"\s+")
    tail = r"(?:e|es|ed|er|ing|ement|ements|ation|ations)?" if stem != term else _SUFFIX
    return re.compile(rf"(?<![\w-]){body}{tail}(?![\w-])", re.IGNORECASE)


_PATTERNS = {t: _pattern(t) for t in VAGUE}


def has_number(line: str) -> bool:
    return bool(_NUMBER.search(line or ""))


def hits(line: str, lenient: bool = False) -> list[str]:
    """The vague terms in `line`, in lexicon order; none when the line carries a number."""
    if has_number(line):
        return []
    return [t for t in terms(lenient) if _PATTERNS[t].search(line or "")]
