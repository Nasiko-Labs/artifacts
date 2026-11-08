"""Pure: pasted tables, parsed in code. A ledger paste (long `month, account, amount, currency` or wide `month,
revenue, refunds, fees, currency`) becomes ledger lines mapped to the rollup's items by account name; a pasted
monthly summary (`month, currency, gross, refunds, disputes, fees, net, invoiced_paid`) becomes rollup rows.
Decimals become minor units half-up. The paste is data: nothing in it is ever read as an instruction."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

import months as m

REVENUE, REFUNDS, FEES = "revenue", "refunds", "fees"
_REFUND_WORDS = re.compile(r"refund", re.IGNORECASE)
_FEE_WORDS = re.compile(r"\bfees?\b|processing|merchant", re.IGNORECASE)
_REVENUE_WORDS = re.compile(r"revenue|sales", re.IGNORECASE)
_MONTH_HEADERS = frozenset({"month", "period", "date"})
_ACCOUNT_HEADERS = frozenset({"account", "category", "account name", "line", "item"})
_AMOUNT_HEADERS = frozenset({"amount", "total", "value", "balance"})
_CURRENCY_HEADERS = frozenset({"currency", "ccy", "cur"})
_SUMMARY_KEYS = ("gross", "refunds", "disputes", "fees", "net", "invoiced_paid", "payouts")
_DELIMITERS = ("\t", ";", "|", ",")
_CURRENCY_CODE = re.compile(r"^[A-Za-z]{3}$")
_MONTH_NAME = re.compile(r"^([A-Za-z]{3,9})\.?[\s\-/']*(\d{2}|\d{4})$")
_ISO_MONTH = re.compile(r"^(\d{4})-(\d{1,2})(?:-\d{1,2})?$")
_SLASH_MONTH = re.compile(r"^(\d{1,2})/(\d{4})$")
_ABBR = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_SYMBOLS = re.compile(r"[$€£¥₹\s]|[A-Za-z]{3}$|^[A-Za-z]{3}")


class LedgerLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    month: str
    account: str
    item: str | None          # revenue | refunds | fees | None (unmapped)
    amount_minor: int
    currency: str | None = None


def to_minor(text: str) -> int | None:
    """"$47,000.00" / "(900.00)" / "-1,512.4" -> minor units, half-up; None when not a number."""
    raw = (text or "").strip()
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = _SYMBOLS.sub("", raw.strip("()")).replace(",", "")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    minor = int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return -minor if negative else minor


def month_of(text: str) -> str | None:
    """"2026-03", "2026-03-31", "Mar 2026", "March-26", "03/2026" -> "2026-03"."""
    cell = (text or "").strip()
    iso = _ISO_MONTH.match(cell)
    if iso and 1 <= int(iso.group(2)) <= 12:
        return m.key(int(iso.group(1)), int(iso.group(2)))
    slash = _SLASH_MONTH.match(cell)
    if slash and 1 <= int(slash.group(1)) <= 12:
        return m.key(int(slash.group(2)), int(slash.group(1)))
    named = _MONTH_NAME.match(cell)
    if named and named.group(1)[:3].casefold() in _ABBR:
        year = int(named.group(2))
        return m.key(year if year > 99 else 2000 + year, _ABBR.index(named.group(1)[:3].casefold()) + 1)
    return None


def item_for(account: str) -> str | None:
    if _REFUND_WORDS.search(account):
        return REFUNDS
    if _FEE_WORDS.search(account):
        return FEES
    if _REVENUE_WORDS.search(account):
        return REVENUE
    return None


def _tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Every block of delimited lines that starts with a header naming a month column."""
    tables: list[tuple[list[str], list[list[str]]]] = []
    header: list[str] | None = None
    body: list[list[str]] = []
    delimiter = ","
    for line in (text or "").splitlines():
        stripped = line.strip().strip("`")
        if not stripped:
            continue
        found = _header(stripped)
        if found is not None:
            if header is not None:
                tables.append((header, body))
            (header, delimiter), body = found, []
            continue
        if header is not None:
            cells = [c.strip() for c in next(csv.reader(io.StringIO(stripped), delimiter=delimiter,
                                                        skipinitialspace=True), [])]
            if cells and month_of(cells[0]) is not None:
                body.append(cells)
    if header is not None:
        tables.append((header, body))
    return [t for t in tables if t[1]]


def _header(line: str) -> tuple[list[str], str] | None:
    for delimiter in _DELIMITERS:
        if delimiter in line:
            cells = [c.strip().casefold().replace("_", " ") for c in line.split(delimiter)]
            if cells and cells[0] in _MONTH_HEADERS and len(cells) >= 2:
                return cells, delimiter
    return None


def _column(header: Sequence[str], names: frozenset[str]) -> int | None:
    return next((i for i, h in enumerate(header) if h in names), None)


def _currency(cells: Sequence[str], index: int | None) -> str | None:
    if index is None or index >= len(cells):
        return None
    value = cells[index].strip()
    return value.upper() if _CURRENCY_CODE.match(value) else None


def _join_amount(cells: list[str], header: Sequence[str], amount_index: int) -> list[str]:
    """`2026-03, Revenue, 47,000.00, USD` split a thousands separator into two cells: join them back."""
    extra = len(cells) - len(header)
    if extra <= 0:
        return cells
    joined = ",".join(cells[amount_index:amount_index + extra + 1])
    return [*cells[:amount_index], joined, *cells[amount_index + extra + 1:]]


def parse_ledger(text: str) -> tuple[LedgerLine, ...]:
    """Ledger lines from every long or wide table in the paste; a summary table is not a ledger."""
    lines: list[LedgerLine] = []
    for header, body in _tables(text):
        if is_summary_header(header):
            continue
        account_i, amount_i = _column(header, _ACCOUNT_HEADERS), _column(header, _AMOUNT_HEADERS)
        currency_i = _column(header, _CURRENCY_HEADERS)
        if account_i is not None and amount_i is not None:
            lines.extend(_long(body, header, account_i, amount_i, currency_i))
        else:
            lines.extend(_wide(body, header, currency_i))
    return tuple(lines)


def _long(body, header, account_i: int, amount_i: int, currency_i: int | None) -> list[LedgerLine]:
    out: list[LedgerLine] = []
    for raw_cells in body:
        cells = _join_amount(raw_cells, header, amount_i)
        if max(account_i, amount_i) >= len(cells):
            continue
        amount, account = to_minor(cells[amount_i]), cells[account_i]
        if amount is None or not account:
            continue
        out.append(LedgerLine(month=month_of(cells[0]), account=account, item=item_for(account),
                              amount_minor=amount, currency=_currency(cells, currency_i)))
    return out


def _wide(body, header, currency_i: int | None) -> list[LedgerLine]:
    out: list[LedgerLine] = []
    for cells in body:
        for i, name in enumerate(header[1:], start=1):
            if i == currency_i or i >= len(cells):
                continue
            amount = to_minor(cells[i])
            if amount is not None:
                out.append(LedgerLine(month=month_of(cells[0]), account=name.title(), item=item_for(name),
                                      amount_minor=amount, currency=_currency(cells, currency_i)))
    return out


def is_summary_header(header: Sequence[str]) -> bool:
    keys = {h.replace(" ", "_") for h in header}
    return {"gross", "net"} <= keys


def parse_summary(text: str) -> tuple[dict, ...]:
    """Pasted monthly summary rows as {month, currency, gross, ... in minor units}."""
    rows: list[dict] = []
    for header, body in _tables(text):
        if not is_summary_header(header):
            continue
        keys = [h.replace(" ", "_") for h in header]
        currency_i = _column(header, _CURRENCY_HEADERS)
        for cells in body:
            row: dict = {"month": month_of(cells[0]), "currency": _currency(cells, currency_i) or "USD"}
            for i, name in enumerate(keys):
                if name in _SUMMARY_KEYS and i < len(cells):
                    value = to_minor(cells[i])
                    if value is not None:
                        row[name] = abs(value) if name in ("refunds", "disputes", "fees", "payouts") else value
            if "gross" in row and "net" in row:
                rows.append(row)
    return tuple(rows)
