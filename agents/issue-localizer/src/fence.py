"""Pure: untrusted text (the issue, comments, file code, tool output) wrapped in data fences before a
model sees it. Fence markers already inside the text are removed first, so the data cannot close its
own fence and talk to the model as instructions."""

from __future__ import annotations

import re

MAX_FENCED_CHARS = 12_000
_MARKER = re.compile(r"<<<\s*(?:END|INPUT|COMMENTS|CANDIDATES|SKELETONS|CODE|ISSUE)[A-Z_]*\s*>>>", re.IGNORECASE)


def strip_markers(text: str) -> str:
    return _MARKER.sub("", text or "")


def cap(text: str, limit: int = MAX_FENCED_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def fence(label: str, text: str, limit: int = MAX_FENCED_CHARS) -> str:
    """`<<<LABEL>>>` … `<<<END>>>` around the cleaned, capped text."""
    return f"<<<{label}>>>\n{cap(strip_markers(text), limit)}\n<<<END>>>"
