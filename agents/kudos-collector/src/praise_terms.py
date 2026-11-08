"""Pure: the code-side praise filter and quote truncation. The model only judges relevance (praise
about work, not a joke) after this filter has passed a message."""

from __future__ import annotations

import re

PRAISE_PHRASES = (
    "thank", "thanks", "great work", "great job", "awesome", "kudos", "shoutout", "shout-out", "shout out",
    "nice catch", "well done", "saved us", "huge help", "appreciate", "amazing", ":tada:", ":clap:",
    ":raised_hands:", ":pray:",
)
_PRAISE = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(p) for p in PRAISE_PHRASES) + r")", re.IGNORECASE)
MAX_QUOTE_CHARS = 280
ELLIPSIS = "…"


def has_praise_phrase(text: str) -> bool:
    return bool(_PRAISE.search(text or ""))


def truncate_quote(quote: str) -> str:
    """At most MAX_QUOTE_CHARS, cut on a word boundary with an ellipsis."""
    text = quote.strip()
    if len(text) <= MAX_QUOTE_CHARS:
        return text
    cut = text[:MAX_QUOTE_CHARS - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip(" ,;:.-") + ELLIPSIS
