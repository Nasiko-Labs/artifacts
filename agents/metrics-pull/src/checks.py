"""Pure text checks for the verbatim rule: a value the model returns (a sheet id or title, a filter value)
must occur in this turn's tool output, the pasted table, or the user's own words. The post-check is the
guarantee, not the prompt. (Pattern copied from the hardened kit; owned by this agent.)"""

from __future__ import annotations

import re
from typing import Any

_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a value says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: Any, seen: str) -> bool:
    """`needle` occurs in already-normalised text. Empty never matches."""
    wanted = normalise_text(text_of(needle)).strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
