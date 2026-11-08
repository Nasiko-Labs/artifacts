"""Pure text checks shared by the post-checks: is a value really in this turn's tool output, reading
times, and reading the model's JSON. The post-check is the guarantee, not the prompt."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"
_LIVEMODE = re.compile(r"[\"']?livemode[\"']?\s*[:=]\s*(true|false)", re.IGNORECASE)
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a value says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str | None, seen: str) -> bool:
    """`needle` (an id or a name) occurs in normalised tool output. Empty never matches."""
    wanted = normalise_text(needle or "").strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def number_in(value: int | None, raw: str) -> bool:
    """The integer occurs in the raw output as a whole number (not inside a longer number). Zero counts
    when a literal 0 is present."""
    if value is None or value < 0:
        return False
    return re.search(rf"(?<![\d.]){value}(?![\d])", raw or "") is not None


def parse_time(value: Any) -> datetime | None:
    """An ISO date/datetime or epoch seconds, as an aware UTC datetime."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = str(int(value))
    text = str(value or "").strip()
    epoch = _EPOCH.match(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def time_backed(value: Any, parsed: datetime | None, raw: str, seen: str) -> bool:
    """A time the model returned is in the tool output, as epoch seconds or as the text it gave."""
    if parsed is None:
        return False
    return number_in(int(parsed.timestamp()), raw) or appears_in(str(value), seen)


def livemode(seen_text: str) -> bool | None:
    """From the tool output itself: False when every record says test mode, None when none says."""
    flags = {m.group(1).lower() for m in _LIVEMODE.finditer(seen_text or "")}
    if not flags:
        return None
    return "true" in flags


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
    return str(value).strip() if value is not None else ""


def int_of(value: Any) -> int | None:
    """An integer, or None. Floats with a fraction and booleans are not integers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None
