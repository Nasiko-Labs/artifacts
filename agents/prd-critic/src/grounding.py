"""Pure: is a quote verbatim in the PRD? Comparison ignores case, whitespace, curly quotes, dashes and
markdown emphasis, nothing else: a paraphrase is not a quote."""

from __future__ import annotations

import re
from collections.abc import Sequence

MIN_QUOTE_CHARS = 8
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
_EMPHASIS = re.compile(r"\*\*|__|`")
_LINE_PREFIX = re.compile(r"^\s*L\d{1,4}\s*:\s*")


def normalise(text: str) -> str:
    text = _EMPHASIS.sub("", (text or "").translate(_TRANSLATE)).casefold()
    return " ".join(text.split())


def clean_quote(quote: str) -> str:
    text = _LINE_PREFIX.sub("", str(quote or "")).strip()
    return text.strip('"').strip("'").strip()


def locate(quote: str, lines: Sequence[str]) -> int | None:
    """The 1-based PRD line that holds `quote` verbatim, or None."""
    needle = normalise(clean_quote(quote))
    if len(needle) < MIN_QUOTE_CHARS:
        return None
    for n, line in enumerate(lines, start=1):
        if needle in normalise(line):
            return n
    return None
