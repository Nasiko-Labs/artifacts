"""Pure: what the user's message asks for, parsed in code (never by the model): the period, an optional
tier / customer / priority filter, JSON output, and the theme names an envelope from an earlier workflow
step lists in its `items[]` (read for follow-ups only)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from config import PRIORITIES, TIERS, Config, norm_priority
from parse_paste import request_part
from period import Period, explicit_period, parse_period
from records import Ticket, load_json_object, text_of

_TIER_FIELD = re.compile(r"\btier\s*:\s*([A-Za-z]+)", re.IGNORECASE)
_TIER_WORD = re.compile(rf"\b({'|'.join(TIERS)})\b(?=\s+(?:customers?|tickets?|accounts?|tier|plan|clients?))",
                        re.IGNORECASE)
_CUSTOMER = re.compile(r"\b(?:customer|organization|org)\s*:\s*([^,;\n]+)", re.IGNORECASE)
_PRIORITY_FIELD = re.compile(r"\bpriority\s*:\s*([A-Za-z0-9]+)", re.IGNORECASE)
_PRIORITY_WORD = re.compile(rf"\b({'|'.join(PRIORITIES)})(?:[- ]priority)?\s+tickets?\b", re.IGNORECASE)
_JSON = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_JSON_FILLER = re.compile(r"\b(?:give|me|it|that|this|the|same|digest|result|results|please|show|return|output|"
                          r"now|again|can|you|send|as|json|only|in|format|and)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Filters:
    tier: str = ""
    customer: str = ""
    priority: str = ""

    @property
    def active(self) -> bool:
        return bool(self.tier or self.customer or self.priority)

    def keeps(self, ticket: Ticket) -> bool:
        if self.tier and self.tier not in ticket.tier_hint.casefold():
            return False
        if self.customer and self.customer.casefold() not in ticket.organization.casefold():
            return False
        return not self.priority or norm_priority(ticket.priority) == self.priority

    def describe(self) -> str:
        parts = [f"tier {self.tier}" if self.tier else "", f"customer {self.customer}" if self.customer else "",
                 f"priority {self.priority}" if self.priority else ""]
        return ", ".join(p for p in parts if p)


@dataclass(frozen=True)
class Request:
    period: Period
    explicit: bool                    # the words named a period
    filters: Filters = Filters()
    as_json: bool = False
    ambiguous: bool = False           # a tier or priority filter we do not know
    envelope_themes: tuple[str, ...] = ()


def wants_json(text: str) -> bool:
    return bool(_JSON.search(text or ""))


def only_json(text: str) -> bool:
    """"as json" with nothing else asked: the stored digest is re-sent as the envelope."""
    return wants_json(text) and not re.sub(r"[\W_]+", "", _JSON_FILLER.sub("", text or ""))


def envelope_themes(text: str) -> tuple[str, ...]:
    data = load_json_object(text or "")
    items = data.get("items") if data else None
    if not isinstance(items, list):
        return ()
    return tuple(n for n in (text_of(i.get("name")) for i in items if isinstance(i, dict)) if n)


def _filters(plain: str) -> tuple[Filters, bool]:
    field, word = _TIER_FIELD.search(plain), _TIER_WORD.search(plain)
    tier = (field.group(1) if field else word.group(1) if word else "").casefold()
    pfield, pword = _PRIORITY_FIELD.search(plain), _PRIORITY_WORD.search(plain)
    raw_priority = (pfield.group(1) if pfield else pword.group(1) if pword else "").casefold()
    priority = norm_priority(raw_priority) if raw_priority else ""
    known_priority = not raw_priority or raw_priority in PRIORITIES or priority != "normal" or raw_priority in (
        "medium", "p3")
    customer = _CUSTOMER.search(plain)
    filters = Filters(tier=tier if tier in TIERS else "", customer=customer.group(1).strip() if customer else "",
                      priority=priority if known_priority else "")
    return filters, (bool(tier) and tier not in TIERS) or not known_priority


def parse_request(query: str, today: date, config: Config) -> Request:
    text = query or ""
    themes = envelope_themes(text)
    plain = "" if themes else request_part(text)
    filters, ambiguous = _filters(plain)
    return Request(period=parse_period(plain, today, config),
                   explicit=explicit_period(plain, today, config) is not None, filters=filters,
                   as_json=wants_json(text), ambiguous=ambiguous, envelope_themes=themes)
