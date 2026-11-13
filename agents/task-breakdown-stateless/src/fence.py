"""Pure: user text and pasted specs are data. Each block sent to the model is fenced between
<<<NAME>>> and >>>END_NAME<<< markers, and any marker already inside the text is stripped first, so a
pasted spec cannot close its own fence and talk to the model as instructions."""

from __future__ import annotations

import re

_MARKER = re.compile(r"<<<\s*[A-Z_]{2,40}\s*>>>|>>>\s*END_[A-Z_]{2,40}\s*<<<", re.IGNORECASE)


def strip_markers(text: str) -> str:
    return _MARKER.sub("", text or "")


def fence(name: str, text: str) -> str:
    return f"<<<{name}>>>\n{strip_markers(text)}\n>>>END_{name}<<<"
