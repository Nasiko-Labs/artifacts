"""Pure: untrusted text (pasted notes, data, the stored deck) wrapped in markers the model is told to
treat as data. Markers already inside the text are stripped, so pasted text cannot close the fence."""

from __future__ import annotations

from inputs import strip_markers


def fence(kind: str, text: str) -> str:
    tag = kind.upper()
    return f"<<<{tag}>>>\n{strip_markers(text)}\n<<<END {tag}>>>"
