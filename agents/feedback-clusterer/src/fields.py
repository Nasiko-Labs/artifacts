"""Pure: one feedback field at a time, parsed in code. ARR amounts, tier words, dates and customer names
are normalised here and nowhere else, so every count and weight downstream rests on the same rules."""

from __future__ import annotations

import re
from datetime import date, datetime

TIER_WEIGHTS = {"enterprise": 5.0, "mid-market": 3.0, "smb": 1.0, "free": 0.5, "unknown": 1.0}
_TIER_WORDS = {
    "enterprise": "enterprise", "ent": "enterprise", "large": "enterprise", "strategic": "enterprise",
    "mid-market": "mid-market", "midmarket": "mid-market", "mid market": "mid-market", "mid": "mid-market",
    "mm": "mid-market", "business": "mid-market", "growth": "mid-market",
    "smb": "smb", "small": "smb", "small business": "smb", "starter": "smb", "team": "smb", "pro": "smb",
    "free": "free", "trial": "free", "freemium": "free", "community": "free",
}
_AMOUNT = re.compile(r"^\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([km])?$", re.IGNORECASE)
_MONEY_SHAPE = re.compile(r"^\$\s*\d|^\d[\d.,]*\s*[km]$", re.IGNORECASE)
_ISO_DATE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_COMPANY_SUFFIX = re.compile(r"(?:[\s,]+(?:inc|llc|ltd|corp|corporation|co|gmbh|plc|limited))+\.?$")
_NOT_NAME = re.compile(r"[^\w@.\-& ]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def parse_arr(raw: object) -> float | None:
    """`120000`, `$120k`, `1.2M`, `120,000` -> a number; anything else -> None (never a guess)."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw) if raw >= 0 else None
    match = _AMOUNT.match(str(raw).strip().replace(" ", ""))
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    suffix = (match.group(2) or "").casefold()
    return value * {"k": 1_000, "m": 1_000_000}.get(suffix, 1)


def looks_like_amount(raw: str) -> bool:
    """A `$` amount or a `k`/`m` amount: how a prefixed field is told to be ARR."""
    text = raw.strip()
    return bool(_MONEY_SHAPE.match(text)) and parse_arr(text) is not None


def normalise_tier(raw: object) -> str:
    text = " ".join(str(raw or "").casefold().replace("_", " ").split())
    return _TIER_WORDS.get(text, _TIER_WORDS.get(text.replace(" ", "-"), "unknown"))


def is_tier_word(raw: str) -> bool:
    return normalise_tier(raw) != "unknown"


def parse_date(raw: object) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if match := _ISO_DATE.match(text):
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if match := _US_DATE.match(text):
            return date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
        return datetime.strptime(text, "%d %b %Y").date()
    except ValueError:
        return None


def customer_key(raw: object) -> str:
    """`Acme Inc.` and `acme` are one customer: casefold, drop punctuation and company suffixes."""
    text = " ".join(_NOT_NAME.sub(" ", str(raw or "").casefold()).split()).strip(" .-")
    if _EMAIL.fullmatch(text):
        return text
    return _COMPANY_SUFFIX.sub("", text).strip(" .-,")


def contains_email(text: str) -> bool:
    return bool(_EMAIL.search(text or ""))
