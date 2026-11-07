"""Pure: the verbatim checks. A quote, id, link or number the model returns counts only when it occurs in this
turn's input or tool output, compared after normalising case, whitespace, curly quotes and JSON escapes."""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " ",
                             "–": "-", "—": "-"})
_EDGE_PUNCTUATION = " .,;:!?\"'*-•"
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")


def normalise(text: str) -> str:
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str, haystack: str) -> bool:
    """`needle` occurs in `haystack` (both normalised). Empty never matches."""
    wanted = normalise(needle).strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in normalise(haystack)


def unseen_numbers_or_links(text: str, source: str) -> tuple[str, ...]:
    """Numbers and links in `text` that do not occur in `source`: what the model may have invented."""
    found = [*_URL.findall(text or ""), *_NUMBER.findall(_URL.sub(" ", text or ""))]
    return tuple(x for x in found if not appears_in(x, source))
