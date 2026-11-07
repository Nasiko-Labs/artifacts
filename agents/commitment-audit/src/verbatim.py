"""Pure: the checks that tie a model's claim to this turn's tool output, and reading its JSON.

A value the model cites (a key, a status, a date, a quote, a link) counts only when it appears in the
tool output the fence adapter recorded this turn. Case, whitespace, curly quotes and JSON escapes do
not change what a value says, so they are normalised away before comparing."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any

_ISO_DATE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})")
_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def normalise_text(text: str) -> str:
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str | None, seen_normalised: str) -> bool:
    """`needle` occurs in already-normalised tool output. Empty never matches."""
    wanted = normalise_text(needle or "").strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen_normalised


def parse_day(value: Any) -> date | None:
    """An ISO date/datetime or epoch seconds, as a UTC date; None when unreadable."""
    text = str(value) if value is not None else ""
    epoch = _EPOCH.match(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC).date()
    match = _ISO_DATE.match(text)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def load_json_object(text: str) -> dict | None:
    """The first-to-last-brace JSON object in a model reply, code fences tolerated."""
    body = _CODE_FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def text_of(value: Any) -> str:
    return str(value).strip() if value is not None and not isinstance(value, (dict, list)) else ""


def int_or_none(value: Any) -> int | None:
    """An integer the model gave (an integral float is accepted); anything else is None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
