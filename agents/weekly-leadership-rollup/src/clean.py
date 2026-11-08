"""Pure: tool-output text made safe for one line of markdown (no markup, links, line breaks or fence
markers), and capped. Every title, name and quote shown passes through here."""

from __future__ import annotations

import re

MAX_TITLE_CHARS = 80
MAX_CELL_CHARS = 40
MAX_QUOTE_CHARS = 200

_MARKER = re.compile(r"<<<\s*[A-Z_]{2,40}\s*>>>|>>>\s*END_[A-Z_]{2,40}\s*<<<", re.IGNORECASE)
_UNSAFE = re.compile(r"[|\n\r`<>*\[\]_#]+")


def clean(text: str | None, limit: int = MAX_TITLE_CHARS) -> str:
    plain = " ".join(_UNSAFE.sub(" ", _MARKER.sub("", text or "")).split())
    return plain if len(plain) <= limit else plain[: limit - 1].rstrip() + "…"


def safe_url(url: str | None) -> str:
    """A link target only when it is a plain http(s) URL with nothing that could break markdown."""
    text = (url or "").strip()
    if not text.startswith(("https://", "http://")) or re.search(r"[\s()<>\[\]`]", text):
        return ""
    return text


def link(label: str, url: str | None) -> str:
    target = safe_url(url)
    return f"[{label}]({target})" if target else label
