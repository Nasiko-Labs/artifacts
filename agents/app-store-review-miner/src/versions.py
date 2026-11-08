"""Pure: app version normalising and ordering (newest first, `unknown` last)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from records import UNKNOWN_VERSION

_VERSION = re.compile(r"(?<![\d.])v?(\d+(?:\.\d+){0,3})(?![\d])", re.IGNORECASE)


def normalise(raw: object) -> str:
    """`v4.2.1` and `4.2.1 (310)` → `4.2.1`; nothing usable → `unknown`."""
    text = str(raw or "").strip()
    match = _VERSION.search(text)
    return match.group(1) if match else UNKNOWN_VERSION


def key(version: str) -> tuple[int, ...]:
    if version == UNKNOWN_VERSION:
        return ()
    parts = tuple(int(p) for p in version.split("."))
    return parts + (0,) * (4 - len(parts))


def newest_first(versions: Iterable[str]) -> list[str]:
    distinct = set(versions)
    known = sorted((v for v in distinct if v != UNKNOWN_VERSION), key=key, reverse=True)
    return known + ([UNKNOWN_VERSION] if UNKNOWN_VERSION in distinct else [])


def next_older(version: str, versions: Iterable[str]) -> str | None:
    """The closest version below `version` among `versions` (never `unknown`)."""
    if version == UNKNOWN_VERSION:
        return None
    older = [v for v in set(versions) if v != UNKNOWN_VERSION and key(v) < key(version)]
    return max(older, key=key) if older else None
