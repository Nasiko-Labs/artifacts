"""Copied from the meeting-prep agent; owned by the day-planner agent.

Pure: does a value the model cited actually appear in this turn's tool output? And reading the
model's JSON answers. The guarantee lives here, not in a prompt."""

from __future__ import annotations

import json
import re
from typing import Any

_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a sentence says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


class Seen:
    """This step's tool output, normalised once, for repeated membership checks."""

    def __init__(self, raw: str):
        self._text = normalise_text(raw)

    def has(self, needle: object) -> bool:
        """`needle` (a quote, link, id or date string) occurs in the tool output. Empty never matches."""
        wanted = normalise_text(str(needle or "")).strip(_EDGE_PUNCTUATION)
        return bool(wanted) and wanted in self._text


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


def text_of(value: Any, limit: int = 600) -> str:
    """A model-supplied string field, trimmed and bounded; anything else becomes ""."""
    return value.strip()[:limit] if isinstance(value, str) else ""
