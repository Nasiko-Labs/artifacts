"""Pure: the exact strings a narrative may use for a number, and how numbers are read back out of text.

Rules (fixed in code, so the model never rounds): percentages to one decimal; values in the table's units
("12.4 thousand" only when the units say thousand); a million or more as "1.2 million"; otherwise full
digits with separators."""

from __future__ import annotations

import math
import re

from table import Unit

PLAIN = Unit()

_NUMBER = re.compile(r"(?<![\w.])[-−]?\d[\d,]*(?:\.\d+)?")


def _trim(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text


def plain(x: float) -> str:
    """1240300 → "1,240,300"; 1234.567 → "1,235"; 12.5 → "12.5"; 0.4567 → "0.46"."""
    if not math.isfinite(x):
        return "n/a"
    if float(x).is_integer():
        return f"{int(x):,}"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return _trim(f"{x:,.2f}")


def amount(value: float, unit: Unit = PLAIN) -> str:
    """One value as the narrative must write it."""
    if unit.pct:
        return f"{value:.1f}%"
    real = value * unit.scale
    size = abs(real)
    if size >= 1e9:
        body = _trim(f"{real / 1e9:.1f}") + " billion"
    elif size >= 1e6:
        body = _trim(f"{real / 1e6:.1f}") + " million"
    elif unit.scale_word == "thousand" and size >= 1e3:
        body = _trim(f"{real / 1e3:.1f}") + " thousand"
    else:
        body = plain(real)
    if not unit.currency:
        return body
    return f"-{unit.currency}{body[1:]}" if body.startswith("-") else f"{unit.currency}{body}"


def change(delta: float, unit: Unit = PLAIN) -> str:
    """The size of a change, without its sign (the sentence says up or down)."""
    if unit.pct:
        return f"{abs(delta):.1f} percentage points"
    return amount(abs(delta), unit)


def percent(share: float) -> str:
    """A share or a percent change (0..100 scale), without its sign: 41.2%."""
    return f"{abs(share):.1f}%"


def times(ratio: float) -> str:
    return _trim(f"{ratio:.1f}") + " times"


def numbers_in(text: str) -> set[float]:
    """Every number written in `text`, as values: "1,240,300" → 1240300.0; "41.2%" → 41.2; "-3" → 3.0."""
    found = set()
    for token in _NUMBER.findall(text or ""):
        clean = token.replace(",", "").replace("−", "-").lstrip("-")
        try:
            found.add(round(float(clean), 6))
        except ValueError:
            continue
    return found


def number_tokens(text: str) -> list[tuple[str, float]]:
    """Each number as written, with its value (sign dropped: the sentence carries direction in words)."""
    out = []
    for token in _NUMBER.findall(text or ""):
        try:
            out.append((token, round(float(token.replace(",", "").replace("−", "-").lstrip("-")), 6)))
        except ValueError:
            continue
    return out
