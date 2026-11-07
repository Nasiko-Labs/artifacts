"""Pure: what a loop's tools really returned, as something code can check a cited value against.

The haystack is the raw tool output (ANSI stripped) plus every string value decoded from JSON bodies, so a
value the model copied from an escaped JSON string is still found. Matching is whitespace-normalised.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from excerpt import strip_ansi
from jsonutil import parse_json

_SPACES = re.compile(r"\s+")
_HEX = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
_FULL_SHA = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
_SHORT_SHA = re.compile(r"[0-9a-f]{7,39}", re.IGNORECASE)


def _norm(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def _strings(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 40:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child, depth + 1)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield str(value)


class Haystack:
    def __init__(self, texts: Iterable[str]):
        raw = [strip_ansi(t) for t in texts]
        decoded = [s for t in raw for s in _strings(parse_json(t))]
        self._text = _norm("\n".join([*raw, *decoded]))
        self._folded = self._text.casefold()
        self._shas = frozenset(m.casefold() for m in _HEX.findall(self._text))

    def has(self, value: Any) -> bool:
        """The value appears verbatim (whitespace-normalised). Empty never counts."""
        text = _norm(str(value)) if value is not None else ""
        return bool(text) and text in self._text

    def has_folded(self, value: Any) -> bool:
        text = _norm(str(value)).casefold() if value is not None else ""
        return bool(text) and text in self._folded

    def sha_ok(self, sha: str) -> bool:
        """A full 40-hex SHA seen verbatim, or a 7-39 hex prefix of a SHA seen in the output."""
        value = (sha or "").strip().casefold()
        if _FULL_SHA.fullmatch(value):
            return value in self._shas
        if _SHORT_SHA.fullmatch(value):
            return any(seen.startswith(value) for seen in self._shas)
        return False
