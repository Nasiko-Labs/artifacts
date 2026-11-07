"""The code post-check: the guarantee, not the prompt.

Every name, path, title, quote, date and link the model returns must appear in this turn's tool output
(`seen`, normalised once), or the item (or that field) is dropped. Dates are parsed here, in code."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any

_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"
_FENCED_JSON = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_EPOCH = re.compile(r"\d{9,13}(?:\.\d+)?")
_MS_THRESHOLD = 10**11  # larger numbers are milliseconds


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a sentence says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str | None, seen: str) -> bool:
    """`needle` occurs in already-normalised tool output. Empty never matches."""
    wanted = normalise_text(needle or "").strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def parse_day(value: Any) -> date | None:
    """ISO date or datetime, or Unix seconds / milliseconds (chat timestamps carry a fraction)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    text = str(value).strip()
    if _EPOCH.fullmatch(text):
        return _from_epoch(float(text))
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _from_epoch(value: float) -> date | None:
    seconds = value / 1000 if value > _MS_THRESHOLD else value
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def parse_object(text: str) -> dict | None:
    """The JSON object in a model answer (bare, or inside a ```json fence), or None."""
    raw = (text or "").strip()
    fenced = _FENCED_JSON.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def text_of(value: Any) -> str:
    """A model-returned field as a clean string ("" for null / non-scalars)."""
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value).strip()


def safe_link(link: str, seen: str) -> str:
    """A link is kept only when it is an http(s) URL that appears in tool output."""
    return link if link.startswith(("http://", "https://")) and appears_in(link, seen) else ""
