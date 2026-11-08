"""Pure: the contract's commercial terms, checked word for word against the contract text.

A term the model extracted is kept only when its quote appears in the contract text (case and whitespace
normalised) and its value is written in that quote. A date written so that day and month could be swapped
is never guessed: it becomes an ambiguity. Two chunks that state different values for one term are both
kept, as an ambiguity, and the term is not compared.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import amounts

FIELDS = ("customer", "seats", "unit_price", "discount", "total_fee", "start_date", "end_date", "term_months",
          "billing_frequency", "payment_terms_days", "auto_renew")
LABELS = {"customer": "customer", "seats": "seats", "unit_price": "unit price", "discount": "discount",
          "total_fee": "total fee", "start_date": "start date", "end_date": "end date", "term_months": "term",
          "billing_frequency": "billing frequency", "payment_terms_days": "payment terms",
          "auto_renew": "auto-renewal"}
FREQUENCIES = ("monthly", "quarterly", "annual", "upfront")
_FREQUENCY_WORDS = {"monthly": ("month",), "quarterly": ("quarter",),
                    "annual": ("annual", "year", "per annum"), "upfront": ("upfront", "up front", "in advance")}
_PERIOD_WORDS = (("month", re.compile(r"per month|/ ?month|/ ?mo\b|a month|monthly|each month", re.IGNORECASE)),
                 ("quarter", re.compile(r"per quarter|/ ?quarter|a quarter|quarterly", re.IGNORECASE)),
                 ("year", re.compile(r"per year|/ ?year|/ ?yr\b|a year|per annum|annual|yearly|each year", re.IGNORECASE)))
_CODE = re.compile(r"\b([A-Z]{3})\b")
CURRENCIES = frozenset({"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "INR", "SEK", "NOK", "DKK", "NZD", "SGD",
                        "HKD", "BRL", "MXN", "ZAR", "PLN", "CNY", "KRW", "AED", "ILS", "CZK", "HUF"})


@dataclass(frozen=True)
class Term:
    """`value`: int (seats, term_months, payment_terms_days), Decimal (unit_price, total_fee, discount
    percent), date, str (customer, billing_frequency) or bool (auto_renew)."""

    field: str
    value: Any
    quote: str
    currency: str | None = None
    period: str | None = None          # unit_price / total_fee: month | quarter | year | term
    amount: Decimal | None = None      # discount: an amount off instead of (or as well as) a percent

    def shown(self) -> str:
        if self.field == "discount":
            parts = [f"{_plain(self.value)}%" if self.value is not None else "",
                     amounts.money(self.amount, self.currency) if self.amount is not None else ""]
            return " + ".join(p for p in parts if p)
        if self.field in ("unit_price", "total_fee"):
            suffix = f"/{self.period}" if self.period and self.period != "term" else (
                " for the term" if self.period == "term" else "")
            return amounts.money(self.value, self.currency) + suffix
        if self.field == "term_months":
            return f"{self.value} months"
        if self.field == "payment_terms_days":
            return f"net {self.value}"
        if self.field == "auto_renew":
            return "auto-renews" if self.value else "does not auto-renew"
        return self.value.isoformat() if isinstance(self.value, date) else str(self.value)


@dataclass(frozen=True)
class Ambiguity:
    field: str
    reason: str                 # "ambiguous date" | "conflicting values"
    quotes: tuple[str, ...]
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContractTerms:
    terms: Mapping[str, Term]
    ambiguous: tuple[Ambiguity, ...] = ()
    dropped: tuple[str, ...] = ()        # fields whose quote or value was not in the contract text

    def get(self, field: str) -> Term | None:
        return self.terms.get(field)

    @property
    def currency(self) -> str | None:
        for name in ("unit_price", "total_fee", "discount"):
            term = self.terms.get(name)
            if term is not None and term.currency:
                return term.currency
        return None

    def not_stated(self) -> tuple[str, ...]:
        held = set(self.terms) | {a.field for a in self.ambiguous} | set(self.dropped)
        if held & {"end_date", "term_months"}:  # either one states the end
            held = held | {"end_date", "term_months"}
        return tuple(f for f in FIELDS if f not in held and f != "customer")


# --- parsing the model's JSON ---------------------------------------------------------------------

def parse_extraction(text: str) -> Mapping[str, Mapping[str, Any]] | None:
    """The `terms` object of one extraction answer, or None when the answer is not that JSON."""
    data = json_object(text)
    terms = data.get("terms") if isinstance(data, Mapping) else None
    if not isinstance(terms, Mapping):
        return None
    return {k: v for k, v in terms.items() if k in FIELDS and isinstance(v, Mapping)}


def json_object(text: str) -> Any:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.removeprefix("json")
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(body[start:end + 1])
    except ValueError:
        return None


# --- verification ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Checked:
    term: Term | None = None
    ambiguity: Ambiguity | None = None
    dropped: bool = False


def verify(field: str, raw: Mapping[str, Any], contract_text: str) -> Checked:
    """One extracted term against the contract text."""
    quote = str(raw.get("quote") or "").strip()
    if not quote or amounts.normalise(quote) not in amounts.normalise(contract_text):
        return Checked(dropped=True)
    reader = _READERS.get(field)
    return reader(field, raw, quote) if reader else Checked(dropped=True)


def _customer(field: str, raw: Mapping[str, Any], quote: str) -> Checked:
    value = str(raw.get("value") or "").strip()
    ok = value and amounts.normalise(value) in amounts.normalise(quote)
    return Checked(Term(field, value, quote)) if ok else Checked(dropped=True)


def _whole(field: str, raw: Mapping[str, Any], quote: str) -> Checked:
    value = amounts.int_of(raw.get("value"))
    ok = value is not None and value > 0 and Decimal(value) in amounts.numbers_in(quote)
    return Checked(Term(field, value, quote)) if ok else Checked(dropped=True)


def _price(field: str, raw: Mapping[str, Any], quote: str) -> Checked:
    value = amounts.decimal_of(raw.get("amount_decimal"))
    if value is None or value <= 0 or value.normalize() not in amounts.numbers_in(quote):
        return Checked(dropped=True)
    period = period_in(quote)
    if field == "total_fee":
        stated = str(raw.get("period") or "")
        period = period or ("term" if stated == "term" else None)
    return Checked(Term(field, value, quote, currency=currency_in(quote, raw.get("currency")), period=period))


def _discount(field: str, raw: Mapping[str, Any], quote: str) -> Checked:
    numbers = amounts.numbers_in(quote)
    percent = amounts.decimal_of(raw.get("percent"))
    amount = amounts.decimal_of(raw.get("amount"))
    percent = percent if percent is not None and 0 < percent < 100 and percent.normalize() in numbers else None
    amount = amount if amount is not None and amount > 0 and amount.normalize() in numbers else None
    if percent is None and amount is None:
        return Checked(dropped=True)
    if percent is not None and "%" not in quote and "percent" not in quote.casefold():
        return Checked(dropped=True)
    return Checked(Term(field, percent, quote, currency=currency_in(quote, raw.get("currency")) if amount else None,
                        amount=amount))


def _date(field: str, raw: Mapping[str, Any], quote: str) -> Checked:
    written, swappable = amounts.dates_in(quote)
    value = amounts.iso_date(raw.get("iso"))
    if value is not None and value in written:
        return Checked(Term(field, value, quote))
    if raw.get("ambiguous") is True or swappable:
        return Checked(ambiguity=Ambiguity(field, "ambiguous date", (quote,)))
    return Checked(dropped=True)


def _frequency(field: str, raw: Mapping[str, Any], quote: str) -> Checked:
    value = str(raw.get("value") or "")
    lowered = quote.casefold()
    ok = value in FREQUENCIES and any(w in lowered for w in _FREQUENCY_WORDS[value])
    return Checked(Term(field, value, quote)) if ok else Checked(dropped=True)


def _renewal(field: str, raw: Mapping[str, Any], quote: str) -> Checked:
    value = raw.get("value")
    ok = isinstance(value, bool) and "renew" in quote.casefold()
    return Checked(Term(field, value, quote)) if ok else Checked(dropped=True)


_READERS = {"customer": _customer, "seats": _whole, "term_months": _whole, "payment_terms_days": _whole,
            "unit_price": _price, "total_fee": _price, "discount": _discount, "start_date": _date,
            "end_date": _date, "billing_frequency": _frequency, "auto_renew": _renewal}


def period_in(quote: str) -> str | None:
    """The billing period a price quote names; the LAST "per ..." wins ("per user per year" → year)."""
    hits = [(m.start(), name) for name, pattern in _PERIOD_WORDS for m in pattern.finditer(quote)]
    return max(hits)[1] if hits else None


def currency_in(quote: str, claimed: Any) -> str | None:
    """A currency written in the quote: a 3-letter code (the claimed one first) or a symbol."""
    codes = set(_CODE.findall(quote))
    if isinstance(claimed, str) and claimed.upper() in codes:
        return claimed.upper()
    known = sorted(codes & CURRENCIES)
    if known:
        return known[0]
    return next((code for symbol, code in amounts.SYMBOLS.items() if symbol in quote), None)


# --- chunks and merge -----------------------------------------------------------------------------

def check_chunk(raw_terms: Mapping[str, Mapping[str, Any]], contract_text: str) -> ContractTerms:
    kept: dict[str, Term] = {}
    ambiguous: tuple[Ambiguity, ...] = ()
    dropped: tuple[str, ...] = ()
    for field, raw in raw_terms.items():
        checked = verify(field, raw, contract_text)
        if checked.term is not None:
            kept[field] = checked.term
        elif checked.ambiguity is not None:
            ambiguous = (*ambiguous, checked.ambiguity)
        else:
            dropped = (*dropped, field)
    return ContractTerms(kept, ambiguous, dropped)


def merge(chunks: Sequence[ContractTerms]) -> ContractTerms:
    """Same value in several chunks → kept once; different values → both kept as a conflict."""
    by_field: dict[str, tuple[Term, ...]] = {}
    for chunk in chunks:
        for field, term in chunk.terms.items():
            by_field[field] = (*by_field.get(field, ()), term)
    kept: dict[str, Term] = {}
    conflicts: tuple[Ambiguity, ...] = ()
    for field, found in by_field.items():
        distinct = list(dict.fromkeys(_identity(t) for t in found))
        if len(distinct) == 1:
            kept[field] = found[0]
            continue
        firsts = [next(t for t in found if _identity(t) == key) for key in distinct]
        conflicts = (*conflicts, Ambiguity(field, "conflicting values", tuple(t.quote for t in firsts),
                                           tuple(t.shown() for t in firsts)))
    # An ambiguous date in one chunk that another chunk states clearly is not an ambiguity.
    ambiguous = _unique_ambiguities((*conflicts, *(a for c in chunks for a in c.ambiguous if a.field not in kept)))
    held = set(kept) | {a.field for a in ambiguous}
    dropped = tuple(dict.fromkeys(f for c in chunks for f in c.dropped if f not in held))
    return ContractTerms(kept, ambiguous, dropped)


def _identity(term: Term) -> tuple:
    return (term.value, term.currency, term.period, term.amount)


def _unique_ambiguities(found: Iterable[Ambiguity]) -> tuple[Ambiguity, ...]:
    by_field: dict[str, Ambiguity] = {}
    for item in found:
        by_field.setdefault(item.field, item)
    return tuple(by_field.values())


# --- state round trip -----------------------------------------------------------------------------

def to_state(terms: ContractTerms) -> dict:
    """JSON-safe (and protobuf-Struct-safe: numbers as strings)."""
    return {"terms": {f: _term_dict(t) for f, t in terms.terms.items()},
            "ambiguous": [{"field": a.field, "reason": a.reason, "quotes": list(a.quotes), "values": list(a.values)}
                          for a in terms.ambiguous],
            "dropped": list(terms.dropped)}


def _term_dict(t: Term) -> dict:
    value = t.value
    kind = ("date" if isinstance(value, date) else "bool" if isinstance(value, bool) else
            "int" if isinstance(value, int) else "decimal" if isinstance(value, Decimal) else "str")
    return {"value": None if value is None else (value.isoformat() if kind == "date" else str(value)),
            "kind": kind, "quote": t.quote, "currency": t.currency, "period": t.period,
            "amount": None if t.amount is None else str(t.amount)}


def from_state(data: Mapping[str, Any] | None) -> ContractTerms | None:
    if not isinstance(data, Mapping):
        return None
    kept = {f: _term_of(f, d) for f, d in (data.get("terms") or {}).items() if f in FIELDS and isinstance(d, Mapping)}
    ambiguous = tuple(Ambiguity(str(a.get("field")), str(a.get("reason")), tuple(a.get("quotes") or ()),
                                tuple(a.get("values") or ())) for a in data.get("ambiguous") or ())
    return ContractTerms(kept, ambiguous, tuple(str(f) for f in data.get("dropped") or ()))


def _term_of(field: str, d: Mapping[str, Any]) -> Term:
    raw, kind = d.get("value"), d.get("kind")
    value: Any = (None if raw is None else amounts.iso_date(raw) if kind == "date" else raw in (True, "True", "true")
                  if kind == "bool" else int(Decimal(str(raw))) if kind == "int" else Decimal(str(raw))
                  if kind == "decimal" else str(raw))
    amount = d.get("amount")
    return Term(field, value, str(d.get("quote") or ""), d.get("currency") or None, d.get("period") or None,
                Decimal(str(amount)) if amount is not None else None)


def _plain(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    return text
