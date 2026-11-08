"""Pure: what the user's message asks for, parsed in code: the month range, a currency filter, json mode, a pasted
ledger, pasted monthly summary rows, an envelope from a previous step, and whether it is a write request this
read-only agent refuses."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import ledger
import months as m
from ledger import LedgerLine

_JSON_MODE = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_CURRENCY = re.compile(r"\b(?:only|just|in)\s+([A-Za-z]{3})\b")
KNOWN_CURRENCIES = frozenset({"USD", "EUR", "GBP", "CAD", "AUD", "INR", "JPY", "CHF", "SEK", "NZD", "SGD", "BRL",
                              "MXN", "DKK", "NOK", "PLN", "HKD", "ZAR"})
_DOMAIN = re.compile(r"roll ?up|month[- ]end|close|gross|net\b|refund|dispute|fees?\b|payouts?|invoic|revenue|"
                     r"ledger|reconcil|tie[- ]?out|p&l|profit|payments?|balance|sales|volume|totals?", re.IGNORECASE)
_WRITE = re.compile(r"\b(?:book|post|record|create|write|enter|push|journal|issue|void|refund (?:the|this|that|it)|"
                    r"cancel|delete|update|adjust)\b[^.?!]*\b(?:entry|entries|journal|ledger|invoice|payment|charge|"
                    r"payout|credit note|account|it|them|this|that)\b", re.IGNORECASE)
_RANGE_WORDS = re.compile(r"\b(?:last|previous|prior|past)\s+(?:\w+\s+)?months?\b|\bq[1-4]\b|\bytd\b|year to date|"
                          r"this month|\b\d{4}-\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
                          re.IGNORECASE)
_SUMMARY_KEYS = ("gross", "refunds", "disputes", "fees", "net", "invoiced_paid", "payouts")


@dataclass(frozen=True)
class Request:
    range: m.Range
    names_range: bool = False
    currency: str | None = None
    json_mode: bool = False
    ledger: tuple[LedgerLine, ...] = ()
    summary: tuple[dict, ...] = field(default_factory=tuple)
    write_ask: bool = False


def parse(query: str, today: date, metadata: Mapping[str, Any] | None = None) -> Request:
    meta = dict(metadata or {})
    text = query or ""
    prose = without_tables(text)
    envelope_rows = _envelope_rows(text)
    summary = envelope_rows or ledger.parse_summary(text)
    rng = m.parse(str(meta.get("range") or prose), today)
    if summary and not rng.named:
        rng = m.Range(m.span(min(r["month"] for r in summary), max(r["month"] for r in summary)), named=True)
    currency = next((c.upper() for c in _CURRENCY.findall(prose) if c.upper() in KNOWN_CURRENCIES), None)
    json_mode = bool(_JSON_MODE.search(prose)) or str(meta.get("output") or "").casefold() == "json"
    lines = ledger.parse_ledger(text)
    return Request(range=rng, names_range=bool(_RANGE_WORDS.search(prose)) and rng.named, currency=currency,
                   json_mode=json_mode, ledger=lines, summary=summary,
                   write_ask=bool(_WRITE.search(prose)) and not lines and not summary)


def without_tables(text: str) -> str:
    """The message minus pasted table rows, so a row's month is never read as the range."""
    return "\n".join(line for line in (text or "").splitlines()
                     if line.count(",") < 2 and line.count("\t") < 2 and line.count("|") < 2 and line.count(";") < 2)


def mentions_domain(text: str) -> bool:
    return bool(_DOMAIN.search(text or ""))


def _envelope_rows(text: str) -> tuple[dict, ...]:
    """`months[]` from a previous step's envelope (major units), as summary rows in minor units."""
    body = (text or "").strip().removeprefix("```json").removesuffix("```").strip()
    if not body.startswith("{"):
        return ()
    try:
        data = json.loads(body)
    except ValueError:
        return ()
    items = data.get("months") if isinstance(data, Mapping) else None
    if not isinstance(items, list):
        return ()
    rows: list[dict] = []
    for item in items:
        if not isinstance(item, Mapping) or not isinstance(item.get("month"), str):
            continue
        month = ledger.month_of(item["month"])
        values = {k: ledger.to_minor(str(item[k])) for k in _SUMMARY_KEYS if item.get(k) is not None}
        if month and values.get("gross") is not None and values.get("net") is not None:
            rows.append({"month": month, "currency": str(item.get("currency") or "USD").upper(),
                         **{k: v for k, v in values.items() if v is not None}})
    return tuple(rows)
