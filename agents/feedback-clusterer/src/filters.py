"""Pure: the focus in the user's instruction line, applied as a code filter on parsed fields.

Tier ("only enterprise") and window ("last 30 days") filter items; a type ("bugs only") filters themes
after assignment; a topic ("about onboarding") is only a hint for the model. `as of YYYY-MM-DD` sets today."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from items import FeedbackItem

THEME_TYPES = ("bug", "feature_request", "usability", "performance", "pricing_billing", "docs_onboarding",
               "integration", "praise", "other")
_TIER = r"(enterprise|mid-?market|mid market|smb|free)"
_TIER_FOCUS = re.compile(rf"\b(?:only|just)\s+(?:the\s+)?{_TIER}\b|\b{_TIER}(?:\s+(?:customers?|accounts?|tier))?"
                         rf"\s+only\b|\b{_TIER}\s+(?:customers?|accounts?)\b", re.IGNORECASE)
_DAYS = re.compile(r"\b(?:last|past)\s+(\d{1,4})\s+days?\b", re.IGNORECASE)
_PERIOD = re.compile(r"\b(?:last|past)\s+(week|month|quarter|year)\b", re.IGNORECASE)
_PERIOD_DAYS = {"week": 7, "month": 30, "quarter": 90, "year": 365}
_AS_OF = re.compile(r"\bas of (\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_HINT = re.compile(r"\b(?:about|focus(?:ed)? on|related to|regarding)\s+([^.;:\n]{3,80})", re.IGNORECASE)
_TYPE_WORDS = {
    "bug": r"bugs?", "feature_request": r"feature requests?|requests?", "usability": r"usability|ux",
    "performance": r"performance|speed", "pricing_billing": r"pricing|billing", "praise": r"praise",
    "integration": r"integrations?", "docs_onboarding": r"docs|documentation",
}


@dataclass(frozen=True)
class Focus:
    tiers: tuple[str, ...] = ()
    days: int = 0
    types: tuple[str, ...] = ()
    hint: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.tiers or self.days or self.types)

    def describe(self) -> str:
        bits = [f"{'/'.join(self.tiers)} only"] if self.tiers else []
        bits += [f"last {self.days} days"] if self.days else []
        bits += [f"{', '.join(t.replace('_', ' ') for t in self.types)} only"] if self.types else []
        return "; ".join(bits)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict | None) -> Focus:
        data = data or {}
        return Focus(tuple(data.get("tiers") or ()), int(data.get("days") or 0), tuple(data.get("types") or ()),
                     str(data.get("hint") or ""))


def today_from(text: str, clock_today: date) -> date:
    match = _AS_OF.search(text or "")
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return clock_today
    return clock_today


def parse_focus(text: str) -> Focus:
    plain = text or ""
    tiers = sorted({_tier(next(g for g in m.groups() if g)) for m in _TIER_FOCUS.finditer(plain)})
    days = 0
    if match := _DAYS.search(plain):
        days = int(match.group(1))
    elif match := _PERIOD.search(plain):
        days = _PERIOD_DAYS[match.group(1).casefold()]
    types = tuple(t for t, words in _TYPE_WORDS.items()
                  if re.search(rf"\b(?:only|just)\s+(?:the\s+)?(?:{words})\b|\b(?:{words})\s+only\b", plain, re.IGNORECASE))
    hint = _HINT.search(plain)
    return Focus(tuple(tiers), days, types, hint.group(1).strip() if hint else "")


def apply(items: tuple[FeedbackItem, ...], focus: Focus, today: date) -> tuple[FeedbackItem, ...]:
    """Items inside the tier and window filters (an undated item is outside any window)."""
    since = (today - timedelta(days=focus.days)).isoformat() if focus.days else ""
    return tuple(i for i in items
                 if (not focus.tiers or i.tier in focus.tiers) and (not since or (i.date and i.date >= since)))


def _tier(word: str) -> str:
    plain = word.casefold().replace(" ", "-")
    return "mid-market" if plain in ("midmarket", "mid-market") else plain
