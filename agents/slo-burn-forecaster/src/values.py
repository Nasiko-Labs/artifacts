"""Pure: times and numbers as users paste them. Unix seconds (UTC) from ISO 8601, `YYYY-MM-DD HH:MM`, Unix
seconds or milliseconds (naive times are UTC); Decimal counts with optional thousands separators; error rates
as fractions or percent strings."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_UNIX = re.compile(r"^\d{9,10}(?:\.\d+)?$")
_UNIX_MS = re.compile(r"^\d{13}$")


def parse_time(raw: Any) -> int | None:
    """Unix seconds (UTC) from ISO 8601, `YYYY-MM-DD HH:MM`, Unix seconds or milliseconds; naive times are UTC."""
    if isinstance(raw, bool):
        return None
    text = str(raw).strip()
    if _UNIX.match(text):
        return int(Decimal(text))
    if _UNIX_MS.match(text):
        return int(text) // 1000
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return int(when.timestamp())


def number(raw: Any, thousands: bool) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip()
    if thousands:
        text = text.replace(",", "")
    text = text.replace("_", "").replace(" ", "")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def rate(raw: Any, thousands: bool) -> Decimal | None:
    text = str(raw).strip() if raw is not None else ""
    if text.endswith("%"):
        value = number(text[:-1], thousands)
        return None if value is None else value / 100
    return number(text, thousands)
