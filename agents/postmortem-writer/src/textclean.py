"""Pure: model text made safe to render: one line, no images, no HTML, a link only when its URL is in the
user's own text; word and snippet caps."""

from __future__ import annotations

import re
from typing import Any

SNIPPET_MAX_CHARS = 200
FIELD_MAX_CHARS = 2_000
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_HTML = re.compile(r"<[^>]{1,500}>")
_SPACES = re.compile(r"\s+")
_CLAUSE_ENDS = (". ", "; ", ", ", " - ", ": ")


def clean_text(text: Any, source: str) -> str:
    """One line of model text: no images, no HTML, a link only when its URL is in the source."""
    if text is None or str(text).strip().casefold() in ("null", "none"):
        return ""
    value = _IMAGE.sub("", str(text)[:FIELD_MAX_CHARS])
    value = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), value)
    return _SPACES.sub(" ", _HTML.sub("", value).replace("|", "/")).strip()


def cut_words(text: str, cap: int) -> str:
    words = text.split()
    if len(words) <= cap:
        return text
    head = " ".join(words[:cap])
    cut = max(head.rfind(mark) for mark in _CLAUSE_ENDS)
    return (head[:cut] if cut >= len(head) // 2 else head).rstrip(" ,;:-.") + "…"


def cap_snippet(snippet: str) -> str:
    return snippet if len(snippet) <= SNIPPET_MAX_CHARS else snippet[:SNIPPET_MAX_CHARS].rstrip() + "…"
