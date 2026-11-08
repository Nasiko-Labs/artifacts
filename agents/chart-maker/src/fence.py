"""Pure: fencing untrusted text (pasted data, a described process) before the model sees it.

Every <<<NAME>>> / >>>END_NAME<<< marker is stripped from the body first, so pasted text cannot close
the fence early or open a fake one."""

from __future__ import annotations

import re

_MARKER = re.compile(r"<<<\s*[A-Z_]{2,40}\s*>>>|>>>\s*END_[A-Z_]{2,40}\s*<<<", re.IGNORECASE)


def strip_markers(text: str) -> str:
    return _MARKER.sub("", text or "")


def fence(name: str, body: str) -> str:
    return f"<<<{name}>>>\n{strip_markers(body)}\n>>>END_{name}<<<"
