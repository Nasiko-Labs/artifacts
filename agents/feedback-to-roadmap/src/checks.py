"""Pure: the verbatim checks. Every id, url, name, quote and amount the model returns must appear in this
turn's tool output (`seen_text`) or in the user's own input, or it is dropped. Tool output is often JSON,
so comparison is on a normalised form (escapes undone, whitespace collapsed, case folded)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\u2019", "'"), ("’", "'"))
_SPACES = re.compile(r"\s+")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_EPOCH = re.compile(r"^\d{9,10}(?:\.\d+)?$")
_URL = re.compile(r"^https?://\S+$")


def normalise(text: str) -> str:
    out = text or ""
    for old, new in _ESCAPES:
        out = out.replace(old, new)
    return _SPACES.sub(" ", out).strip().casefold()


@dataclass(frozen=True)
class Seen:
    """This turn's evidence, normalised once."""

    raw: str
    norm: str

    @staticmethod
    def of(text: str) -> Seen:
        return Seen(text or "", normalise(text))

    def has(self, value: object) -> bool:
        """The value occurs verbatim (normalised) in the evidence. Empty never counts."""
        text = normalise(str(value if value is not None else ""))
        return bool(text) and text in self.norm

    def has_exact(self, value: object) -> bool:
        """Ids and urls: exact (case-sensitive) occurrence, escapes undone."""
        text = str(value if value is not None else "").strip()
        if not text:
            return False
        return text in self.raw or text in self.raw.replace("\\/", "/")


def checked_url(url: object, seen: Seen) -> str:
    text = str(url or "").strip()
    return text if _URL.match(text) and seen.has_exact(text) else ""


def parse_day(raw: object) -> date | None:
    """An ISO date or datetime, or a unix timestamp (chat message ids are often timestamps)."""
    text = str(raw or "").strip()
    if not text:
        return None
    if _EPOCH.match(text):
        try:
            return datetime.fromtimestamp(float(text), UTC).date()
        except (OverflowError, ValueError):
            return None
    match = _ISO.search(text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def is_epoch(raw: object) -> bool:
    return bool(_EPOCH.match(str(raw or "").strip()))


def seen_day(raw: object, seen: Seen) -> date | None:
    """The date, only when the evidence backs it."""
    return parse_day(raw) if date_seen(raw, seen) else None


def date_seen(raw: object, seen: Seen) -> bool:
    """A date counts only when its text (or its ISO day) is in the evidence."""
    text = str(raw or "").strip()
    day = parse_day(text)
    return day is not None and (seen.has_exact(text) or seen.has_exact(day.isoformat()) or bool(_EPOCH.match(text))
                                and seen.has_exact(text.split(".")[0]))
