"""Pure: how numbers are written, and how numbers are read back out of a sentence (so a hypothesis's
numbers can be traced to the evidence it cites)."""

from __future__ import annotations

import math
import re

_NUMBER = re.compile(r"(?<![\w.])[-−+]?\d[\d,]*(?:\.\d+)?%?")


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


def signed(value: float) -> str:
    """+1,200 / -300 / 0."""
    text = fmt(value)
    return text if text.startswith("-") or text in ("0", "n/a") else f"+{text}"


def pct(share: float) -> str:
    """A share already in percent (0..100) with one decimal: 41.2%."""
    return f"{float(share):.1f}%"


def rate(fraction: float) -> str:
    """A rate (0..1) as a percentage with two decimals: 0.048 → 4.80%."""
    return f"{float(fraction) * 100:.2f}%"


def pp(fraction: float) -> str:
    """A rate difference (fraction) in percentage points: -0.0068 → -0.68 pp."""
    value = float(fraction) * 100
    text = f"{value:.2f}"
    if text in ("-0.00", "0.00"):
        return "0.00 pp"
    return f"{'+' if value > 0 else ''}{text} pp"


def _trim(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text


def numbers_in(text: str) -> set[float]:
    """Every number written in `text`, as absolute values: "1,240,300" → 1240300.0; "-0.68" → 0.68."""
    found = set()
    for token in _NUMBER.findall(text or ""):
        clean = token.replace(",", "").replace("%", "").replace("−", "").replace("-", "").replace("+", "")
        try:
            found.add(round(float(clean), 6))
        except ValueError:
            continue
    return found
