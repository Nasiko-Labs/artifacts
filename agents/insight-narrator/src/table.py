"""Pure: typed columns from sniffed records. Header detection, the row/column caps, type inference
(period label, number, category) and units from the header, the cells and the user's instruction."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from cells import is_time, parse_number

MAX_ROWS = 200
MAX_COLS = 12
TYPED_SHARE = 0.8       # a column is numeric / a period axis when this share of its non-blank cells parse
SCALES = (("billion", 1e9, re.compile(r"\bbillions?\b|\(\s*(?:[$€£]|usd|eur|gbp)?\s*bn?\s*\)|\bbn\b", re.IGNORECASE)),
          ("million", 1e6, re.compile(r"\bmillions?\b|\(\s*(?:[$€£]|usd|eur|gbp)?\s*(?:m|mn|mm)\s*\)|\bmn\b", re.IGNORECASE)),
          ("thousand", 1e3, re.compile(r"\bthousands?\b|\(\s*(?:[$€£]|usd|eur|gbp)?\s*k\s*\)|\b000s\b|'000|\bin k\b",
                                       re.IGNORECASE)))
_CURRENCY = (("$", re.compile(r"\$|\busd\b|\bdollars?\b", re.IGNORECASE)), ("€", re.compile(r"€|\beur\b|\beuros?\b", re.IGNORECASE)),
             ("£", re.compile(r"£|\bgbp\b|\bpounds?\b", re.IGNORECASE)))
_PCT_HEADER = re.compile(r"%|\bpercent(?:age)?\b|\bpct\b", re.IGNORECASE)


@dataclass(frozen=True)
class Unit:
    currency: str = ""
    scale: float = 1.0
    scale_word: str = ""
    pct: bool = False


@dataclass(frozen=True)
class Column:
    name: str
    kind: str                              # time | numeric | category
    raw: tuple[str, ...]
    values: tuple[float | None, ...] = ()  # numeric columns only
    unit: Unit = Unit()
    unparsed: int = 0                      # non-blank cells a numeric column could not read
    mixed: bool = False                    # some cells in %, some not; or several currencies


@dataclass(frozen=True)
class Table:
    columns: tuple[Column, ...]
    notes: tuple[str, ...] = ()
    header_guessed: bool = False
    suffix_read: bool = False

    @property
    def rows(self) -> int:
        return len(self.columns[0].raw) if self.columns else 0

    def numeric(self) -> list[Column]:
        return [c for c in self.columns if c.kind == "numeric"]

    def labels(self) -> list[Column]:
        return [c for c in self.columns if c.kind != "numeric"]

    def unparsed_share(self) -> float:
        cells = sum(1 for c in self.columns for v in c.raw if v.strip())
        return sum(c.unparsed for c in self.columns) / cells if cells else 0.0


def build(records: Sequence[Sequence[str]], instruction: str = "") -> Table:
    notes: list[str] = []
    width = min(max(len(r) for r in records), MAX_COLS)
    if max(len(r) for r in records) > MAX_COLS:
        notes.append(f"The table has more than {MAX_COLS} columns; I used the first {MAX_COLS}.")
    rows = [tuple((list(r) + [""] * width)[:width]) for r in records]
    guessed = all(parse_number(c) is not None for c in rows[0] if c.strip())
    header = tuple(f"Column {n}" for n in range(1, width + 1)) if guessed else _names(rows[0])
    body = rows if guessed else rows[1:]
    if guessed:
        notes.append("The first row looked like data, so I named the columns Column 1, Column 2 and so on.")
    if len(body) > MAX_ROWS:
        notes.append(f"I used the first {MAX_ROWS} rows; paste a summarised table for the full picture.")
        body = body[:MAX_ROWS]
    columns = tuple(_column(name, tuple(r[i] for r in body), instruction, i) for i, name in enumerate(header))
    suffix = any(_has_suffix(c) for c in columns if c.kind == "numeric")
    if suffix:
        notes.append("I read K, M and B after a number as thousand, million and billion.")
    return Table(columns, tuple(notes), guessed, suffix)


def _names(raw: Sequence[str]) -> tuple[str, ...]:
    seen: dict[str, int] = {}
    names = []
    for n, name in enumerate(raw, start=1):
        clean = " ".join(name.split()) or f"Column {n}"
        count = seen.get(clean.casefold(), 0)
        seen[clean.casefold()] = count + 1
        names.append(clean if count == 0 else f"{clean} ({count + 1})")
    return tuple(names)


def _column(name: str, raw: tuple[str, ...], instruction: str, index: int) -> Column:
    filled = [c for c in raw if c.strip()]
    if not filled:
        return Column(name, "category", raw)
    if index == 0 and sum(is_time(c, name) for c in filled) >= TYPED_SHARE * len(filled):
        return Column(name, "time", raw)
    parsed = [parse_number(c) if c.strip() else None for c in raw]
    good = [p for p in parsed if p is not None]
    if len(good) < TYPED_SHARE * len(filled):
        if sum(is_time(c, name) for c in filled) >= TYPED_SHARE * len(filled):
            return Column(name, "time", raw)
        return Column(name, "category", raw)
    pct_cells = sum(p.pct for p in good)
    currencies = {p.currency for p in good if p.currency}
    pct = pct_cells > len(good) / 2 or bool(_PCT_HEADER.search(name))
    mixed = 0 < pct_cells < len(good) or len(currencies) > 1
    return Column(name, "numeric", raw, tuple(p.value if p else None for p in parsed),
                  _unit(name, instruction, pct, currencies), len(filled) - len(good), mixed)


def _unit(header: str, instruction: str, pct: bool, currencies: set[str]) -> Unit:
    if pct:
        return Unit(pct=True)
    currency = next(iter(currencies)) if len(currencies) == 1 else ""
    currency = currency if currency in ("$", "€", "£") else {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, "")
    for source in (header, instruction):
        currency = currency or next((sym for sym, pattern in _CURRENCY if pattern.search(source)), "")
    for source in (header, instruction):
        for word, scale, pattern in SCALES:
            if pattern.search(source):
                return Unit(currency, scale, word)
    return Unit(currency)


def _has_suffix(column: Column) -> bool:
    return any((p := parse_number(c)) is not None and p.suffix_scale != 1.0 for c in column.raw if c.strip())
