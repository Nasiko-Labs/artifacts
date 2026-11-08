"""Pure: number formatting shared by every renderer (thousands separators, consistent decimals,
percentages). All arithmetic stays in code; the model never formats a number."""

from __future__ import annotations

import re
from collections.abc import Iterable

MAX_DECIMALS = 2
MAX_LABEL_CHARS = 24
ELLIPSIS = "…"
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|#])")
_BACKTICKS = re.compile(r"`+")


def decimals_for(values: Iterable[float]) -> int:
    """The fewest decimals (<= 2) that show every value as parsed."""
    best = 0
    for value in values:
        for places in range(MAX_DECIMALS + 1):
            if abs(round(value, places) - value) < 1e-9:
                best = max(best, places)
                break
        else:
            best = MAX_DECIMALS
    return best


def number(value: float, decimals: int = 0) -> str:
    text = f"{value:,.{decimals}f}"
    return "0" if text in ("-0", "-0.0", "-0.00") else text


def percent(part: float, total: float) -> str:
    if total == 0:
        return "–"
    share = part / total * 100
    return f"{share:.1f}%" if 0 < abs(share) < 1 else f"{round(share)}%"


def short_label(label: str, limit: int = MAX_LABEL_CHARS) -> str:
    return label if len(label) <= limit else label[:limit - 1].rstrip() + ELLIPSIS


def md(text: str) -> str:
    """User text made inert in markdown (no emphasis, links, html or table breaks)."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join((text or "").split()))


def code_block(language: str, body: str) -> str:
    """A fenced block whose fence is longer than any backtick run inside, so the body can't close it."""
    longest = max((len(m) for m in _BACKTICKS.findall(body)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{body}\n{fence}"
