"""Fencing untrusted page content before it reaches the model (owned by this agent).

Fence markers are stripped from the content first, so a page cannot close the fence early and speak
as instructions."""

from __future__ import annotations

import re

PAGE = "PAGE"
_MARKER = re.compile(r"<<<\s*[A-Z_]+\s*>>>|>>>\s*END_[A-Z_]+\s*<<<|<<<|>>>")


def strip_markers(text: str) -> str:
    return _MARKER.sub(" ", text or "")


def fence(text: str, name: str = PAGE) -> str:
    return f"<<<{name}>>>\n{strip_markers(text)}\n>>>END_{name}<<<"
