"""Pure: the reporting period named in a claim or a sheet label (`Q3 2026`, `Q3'26`, `FY26`, `H1`, `Sep`,
`September 2026`, `2026-09`), and whether two periods are the same.

Two periods are the same when their kind and index agree and, when both name a year, the year agrees.
A fiscal year and a calendar year of the same number are treated as the same period.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_WORD = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
               r"|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_YEAR = r"(?:'|’|\s|-|/)?\s*(?:fy\s*)?(\d{4}|\d{2})?"

_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("Q", re.compile(rf"\bq([1-4]){_YEAR}\b", re.IGNORECASE)),
    ("QY", re.compile(r"\b(\d{4})\s*[-/ ]?\s*q([1-4])\b", re.IGNORECASE)),
    ("H", re.compile(rf"\bh([12]){_YEAR}\b", re.IGNORECASE)),
    ("FY", re.compile(r"\bfy\s*'?(\d{4}|\d{2})\b", re.IGNORECASE)),
    ("ISO", re.compile(r"\b(\d{4})-(\d{2})(?:-\d{2})?\b")),
    ("M", re.compile(rf"\b({_MONTH_WORD})\b\.?{_YEAR}\b", re.IGNORECASE)),
    ("Y", re.compile(r"(?<![\d.,$€£])\b((?:19|20)\d{2})\b(?![\d.,%])")),
)


@dataclass(frozen=True)
class Period:
    kind: str               # Q | H | FY | M | Y
    index: int | None = None
    year: int | None = None

    def label(self) -> str:
        year = f" {self.year}" if self.year else ""
        if self.kind in ("Q", "H"):
            return f"{self.kind}{self.index}{year}"
        if self.kind == "M":
            return f"{_MONTHS[self.index - 1].title()}{year}"
        return f"FY{self.year}" if self.kind == "FY" else str(self.year)


def _year(raw: str | None) -> int | None:
    if not raw:
        return None
    value = int(raw)
    return 2000 + value if value < 100 else value


def _month(word: str) -> int:
    return _MONTHS.index(word[:3].casefold()) + 1


def _period(kind: str, match: re.Match) -> Period | None:
    if kind == "Q":
        return Period("Q", int(match.group(1)), _year(match.group(2)))
    if kind == "QY":
        return Period("Q", int(match.group(2)), _year(match.group(1)))
    if kind == "H":
        return Period("H", int(match.group(1)), _year(match.group(2)))
    if kind == "FY":
        return Period("FY", None, _year(match.group(1)))
    if kind == "ISO":
        month = int(match.group(2))
        return Period("M", month, int(match.group(1))) if 1 <= month <= 12 else None
    if kind == "M":
        return Period("M", _month(match.group(1)), _year(match.group(2)))
    return Period("Y", None, int(match.group(1)))


def parse(text: str) -> Period | None:
    """The earliest period written in `text` (a more specific one wins at the same position)."""
    found: list[tuple[int, int, Period]] = []
    for rank, (kind, pattern) in enumerate(_PATTERNS):
        for match in pattern.finditer(text or ""):
            period = _period(kind, match)
            if period is not None:
                found.append((match.start(), rank, period))
    return min(found, key=lambda f: (f[0], f[1]))[2] if found else None


def same(a: Period, b: Period) -> bool:
    years_differ = a.year is not None and b.year is not None and a.year != b.year
    if {a.kind, b.kind} <= {"FY", "Y"}:
        return not years_differ
    return a.kind == b.kind and a.index == b.index and not years_differ
