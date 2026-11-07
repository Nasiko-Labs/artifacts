"""Pure: safe text for the markdown reply, and id lists as the model cites them."""

from __future__ import annotations

import re
from typing import Any

MAX_TEXT_CHARS = 500
_TAG = re.compile(r"<[^>]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_URL = re.compile(r"\bhttps?://[^\s)>\]]+", re.IGNORECASE)


def clean_text(text: str, source: str = "") -> str:
    """No raw HTML, no images, links only when their URL is in the user's input."""
    out = _IMAGE.sub("", str(text or ""))
    out = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), out)
    out = _URL.sub(lambda m: m.group(0) if m.group(0) in source else "", out)
    out = _TAG.sub("", out).replace("<", "‹").replace(">", "›")
    return " ".join(out.split())[:MAX_TEXT_CHARS]


def cell(text: object) -> str:
    """A markdown table cell from data: never a link, an image or HTML; pipes escaped."""
    plain = _TAG.sub("", str(text)).replace("<", "‹").replace(">", "›").replace("|", "\\|")
    plain = plain.replace("](", "] (").replace("![", "! [")
    return " ".join(plain.split())[:80]


def ids_of(raw: Any, prefix: str) -> list[str]:
    """Ids as cited, tolerating slips: one bare string, numbers (2 → e2), case and spaces."""
    items = [raw] if isinstance(raw, str | int) else raw if isinstance(raw, list) else []
    out = []
    for item in items:
        text = str(item).strip().casefold().replace(" ", "")
        out.append(f"{prefix}{text}" if text.isdigit() else text)
    return out
