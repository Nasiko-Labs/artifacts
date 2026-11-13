"""Pure: transcript clock times, as seconds and as shown."""

from __future__ import annotations

import re


def to_secs(value: str) -> int:
    """'12:03' -> 723, '1:02:03.500' -> 3723; -1 when it is not a time."""
    parts = re.split(r"[.,]", value.strip())[0].split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return -1
    secs = 0
    for p in parts:
        secs = secs * 60 + int(p)
    return secs


def show_time(secs: int) -> str:
    if secs < 0:
        return ""
    hours, rest = divmod(secs, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"
