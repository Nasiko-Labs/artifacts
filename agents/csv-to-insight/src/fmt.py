"""Pure: how numbers and dates are written, and how numbers are read back out of a sentence (so a
finding's numbers can be traced to the facts it cites)."""

from __future__ import annotations

import math
import re
from datetime import date, datetime

_NUMBER = re.compile(r"(?<![\w.])[-−]?\d[\d,]*(?:\.\d+)?%?")


def fmt(value: float) -> str:
    """1240300 → "1,240,300"; 1234.567 → "1,235"; 12.5 → "12.5"; 0.4567 → "0.457"."""
    x = float(value)
    if not math.isfinite(x):
        return "n/a"
    if x.is_integer():
        return f"{int(x):,}"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    if abs(x) >= 1:
        return _trim(f"{x:,.2f}")
    return _trim(f"{x:.3f}")


def pct(share: float) -> str:
    """A share (0..100) with one decimal: 41.2%."""
    return f"{float(share):.1f}%"


def day(value: date | datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _trim(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text


def numbers_in(text: str) -> set[float]:
    """Every number written in `text`, as values: "1,240,300" → 1240300.0; "41.2%" → 41.2; "09" → 9.0."""
    found = set()
    for token in _NUMBER.findall(text or ""):
        clean = token.replace(",", "").replace("%", "").replace("−", "-")
        try:
            found.add(round(float(clean), 6))
        except ValueError:
            continue
    return found
