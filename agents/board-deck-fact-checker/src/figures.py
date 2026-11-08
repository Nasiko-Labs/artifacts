"""Pure: read a written number (`$4.2M`, `4.2 million`, `1,234`, `(3.1)`, `12%`, `1.2x`, `€12k`) into its
value, scale, unit and precision, and format a value back in the deck's own style.

`precision` is the place value of the last digit the deck wrote, in the written units: `$4.2M` has
precision 0.1 (million), `1,234` precision 1, `12.5%` precision 0.1. The tolerance rules in compare.py
are built on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

THOUSAND = 1e3
MILLION = 1e6
BILLION = 1e9
TRILLION = 1e12

_SCALES = {
    "k": THOUSAND, "thousand": THOUSAND,
    "m": MILLION, "mm": MILLION, "mn": MILLION, "million": MILLION,
    "b": BILLION, "bn": BILLION, "billion": BILLION,
    "t": TRILLION, "tn": TRILLION, "trillion": TRILLION,
}
_SCALE_SUFFIX = {THOUSAND: "k", MILLION: "M", BILLION: "B", TRILLION: "T"}
_PERCENT = ("%", "percent")
_POINTS = ("pt", "pts", "pp", "point", "points", "ppt", "ppts")
_RATIO = ("x", "×")
_CURRENCY = {"$": "$", "€": "€", "£": "£", "¥": "¥", "usd": "$", "eur": "€", "gbp": "£"}

_NUMBER = re.compile(
    r"(?P<open>\()?\s*(?P<sign>[-−–+])?\s*(?P<cur>[$€£¥]|usd|eur|gbp)?\s*(?P<sign2>[-−–])?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?:\s*(?P<suf>%|percent\b|ppts?\b|pts?\b|pp\b|points?\b|x\b|×|thousand\b|million\b|billion\b|trillion\b"
    r"|mm\b|mn\b|bn\b|tn\b|k\b|m\b|b\b|t\b))?"
    r"\s*(?P<close>\))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Number:
    value: float          # signed, in the written units (4.2 for $4.2M)
    scale: float = 1.0    # 1, 1e3, 1e6, 1e9, 1e12
    unit: str = "count"   # currency | percent | count | ratio
    precision: float = 1.0
    points: bool = False  # written as percentage points ("3 pts")
    symbol: str = ""      # the currency symbol, for display

    @property
    def absolute(self) -> float:
        return self.value * self.scale

    @property
    def absolute_precision(self) -> float:
        return self.precision * self.scale


def parse(text: str, whole: bool = False) -> Number | None:
    """The first number written in `text` (or the whole text when `whole`), or None."""
    raw = (text or "").strip()
    match = _NUMBER.fullmatch(raw) if whole else _NUMBER.search(raw)
    if match is None:
        return None
    return _number(match)


def _number(match: re.Match) -> Number:
    digits = match.group("num").replace(",", "")
    decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
    value = float(digits)
    minus = ("-", "−", "–")
    negative = bool(match.group("open") and match.group("close")) or any(
        match.group(g) in minus for g in ("sign", "sign2"))
    suffix = (match.group("suf") or "").casefold()
    cur = (match.group("cur") or "").casefold()
    unit, scale, points = "count", 1.0, False
    if suffix in _PERCENT:
        unit = "percent"
    elif suffix in _POINTS:
        unit, points = "percent", True
    elif suffix in _RATIO:
        unit = "ratio"
    elif suffix in _SCALES:
        scale = _SCALES[suffix]
    if cur and unit == "count":
        unit = "currency"
    return Number(value=-value if negative else value, scale=scale, unit=unit, precision=10.0 ** -decimals,
                  points=points, symbol=_CURRENCY.get(cur, ""))


def is_cell_number(text: str) -> bool:
    return parse(text, whole=True) is not None


def fmt(absolute: float, like: Number, extra_decimals: int = 2) -> str:
    """`absolute` written in the deck's scale and unit, with up to `extra_decimals` more digits than the deck
    wrote (trailing zeros dropped): 11,960,000 like `$12.4M` -> `$11.96M`."""
    scaled = absolute / like.scale
    decimals = max(0, _decimals(like.precision) + extra_decimals)
    body = f"{abs(scaled):,.{decimals}f}"
    if "." in body:
        body = body.rstrip("0").rstrip(".")
    sign = "-" if scaled < 0 else ""
    if like.unit == "percent":
        return f"{sign}{body}{' pts' if like.points else '%'}"
    if like.unit == "ratio":
        return f"{sign}{body}x"
    return f"{sign}{like.symbol}{body}{_SCALE_SUFFIX.get(like.scale, '')}"


def _decimals(precision: float) -> int:
    count = 0
    while precision < 1 - 1e-12 and count < 12:
        precision *= 10
        count += 1
    return count
