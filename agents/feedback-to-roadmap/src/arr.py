"""Pure: ARR amounts parsed in code (`$1.2M`, `120,000`, `85k`, `120000.00`). Anything else is None, never a
guess. An amount from the CRM counts only when its raw text is in this turn's tool output."""

from __future__ import annotations

import re

from checks import Seen

_AMOUNT = re.compile(r"^(?:usd|\$|€|£)?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([km])?(?:\s*(?:usd|eur|gbp))?$",
                     re.IGNORECASE)


def parse_arr(raw: object) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw) if raw >= 0 else None
    text = str(raw).strip().replace(" ", "")
    match = _AMOUNT.match(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return value * {"k": 1_000, "m": 1_000_000}.get((match.group(2) or "").casefold(), 1)


def checked_arr(raw: object, seen: Seen) -> float | None:
    """The parsed amount only when its raw text (or its plain number form) is in the tool output."""
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    value = parse_arr(text)
    if value is None:
        return None
    plain = [text, f"{value:.0f}", f"{value:.1f}", f"{value:.2f}", f"{value:,.0f}"]
    return value if any(seen.has_exact(p) for p in plain) else None


def money(value: float | None) -> str:
    return "unknown" if value is None else f"{value:,.0f}"
