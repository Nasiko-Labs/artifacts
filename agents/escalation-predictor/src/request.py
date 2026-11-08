"""Pure: what the user's message asks for, parsed in code (never by the model): the window, a tier or
customer filter, `top N`, ticket ids, a draft request, JSON output, and the ticket ids an envelope from an
earlier workflow step names in its `items[]`."""

from __future__ import annotations

import re
from dataclasses import dataclass

from config import Config
from parse_paste import request_part
from records import load_json_object, text_of
from risk import TIERS

_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20}
_NUM = r"(\d{1,3}|" + "|".join(_WORDS) + r")"
_SPAN = re.compile(rf"\b(?:last|past|previous|recent)\s+{_NUM}\s*(days?|weeks?|months?)\b", re.IGNORECASE)
_THIS = re.compile(r"\b(?:this|last|past) (week|month|quarter)\b", re.IGNORECASE)
_TOP = re.compile(rf"\btop\s+{_NUM}\b", re.IGNORECASE)
_TICKET_ID = re.compile(r"#\s*([A-Za-z0-9_-]{1,32})")
_TIER_FIELD = re.compile(r"\btier\s*:\s*([A-Za-z]+)", re.IGNORECASE)
_TIER_WORD = re.compile(rf"\b({'|'.join(TIERS)})\b(?=\s+(?:customers?|tickets?|accounts?|tier|plan|clients?))",
                        re.IGNORECASE)
_CUSTOMER = re.compile(r"\bcustomer\s*:\s*([^,;\n]+)", re.IGNORECASE)
_DRAFT = re.compile(r"\b(?:draft|write|prepare)\b.*\b(?:reply|replies|response|update|answer|message)\b",
                    re.IGNORECASE | re.DOTALL)
_JSON = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_JSON_FILLER = re.compile(r"\b(?:give|me|it|that|this|the|same|list|result|results|please|show|return|output|"
                          r"now|again|can|you|send|as|json|only|in|format|and)\b", re.IGNORECASE)
_PERIODS = {"week": 7, "month": 30, "quarter": 90}


@dataclass(frozen=True)
class Request:
    window_days: int
    top_n: int
    tier: str = ""
    customer: str = ""
    ticket_ids: tuple[str, ...] = ()
    draft: bool = False
    as_json: bool = False
    focus_ids: tuple[str, ...] = ()   # from an input envelope's items[]
    ambiguous: bool = False           # a tier filter we do not know


def _count(word: str) -> int:
    return _WORDS.get(word.casefold()) or int(word)


def window_days(text: str, config: Config) -> int:
    span = _SPAN.search(text)
    if span:
        unit = span.group(2).casefold()
        days = _count(span.group(1)) * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
    else:
        period = _THIS.search(text)
        days = _PERIODS[period.group(1).casefold()] if period else config.window_days
    return max(config.min_window_days, min(config.max_window_days, days))


def wants_json(text: str) -> bool:
    return bool(_JSON.search(text or ""))


def only_json(text: str) -> bool:
    """"as json" with nothing else asked: the stored result is re-sent as the envelope."""
    return wants_json(text) and not re.sub(r"[\W_]+", "", _JSON_FILLER.sub("", text or ""))


def envelope_ids(text: str) -> tuple[str, ...]:
    data = load_json_object(text or "")
    items = data.get("items") if data else None
    if not isinstance(items, list):
        return ()
    ids = (text_of(i.get("ticket_id")).lstrip("#") for i in items if isinstance(i, dict))
    return tuple(dict.fromkeys(i for i in ids if i))


def parse_request(query: str, config: Config) -> Request:
    text = query or ""
    focus = envelope_ids(text)
    plain = "" if focus else request_part(text)
    top = _TOP.search(plain)
    top_n = max(1, min(config.max_top_n, _count(top.group(1)))) if top else config.top_n
    field = _TIER_FIELD.search(plain)
    word = _TIER_WORD.search(plain)
    tier = (field.group(1) if field else word.group(1) if word else "").casefold()
    customer = _CUSTOMER.search(plain)
    return Request(window_days=window_days(plain, config), top_n=top_n, tier=tier if tier in TIERS else "",
                   customer=customer.group(1).strip() if customer else "",
                   ticket_ids=tuple(dict.fromkeys(_TICKET_ID.findall(plain))), draft=bool(_DRAFT.search(plain)),
                   as_json=wants_json(text), focus_ids=focus, ambiguous=bool(tier) and tier not in TIERS)
