"""Pure: find the billing export in the user's message and parse it into rows.

The header line is found in code (≥ 3 delimited fields, the next line has as many); anything before it is the
instruction. Columns are mapped from the synonyms common cloud billing exports use (case-insensitive, spaces,
underscores, slashes and colons ignored). Costs become integer micro-units (cost × 1,000,000, half-up), so every
sum is exact. Negative rows (credits, refunds) are kept. A row whose date or cost cannot be read is a bad row:
counted and shown, never guessed. Amounts are never converted between currencies.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_PASTE_CHARS = 100_000
MIN_FIELDS = 3
MICROS = 1_000_000
DEFAULT_CURRENCY = "USD"
INSTRUCTION_MAX_CHARS = 400
_DELIMITERS = (",", "\t", ";")
_NORM = re.compile(r"[\s_\-/:().\"'\[\]]+")
_MONEY_JUNK = re.compile(r"[\s$€£¥,]")
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FENCE_LINE = re.compile(r"^\s*```")


def norm(name: str) -> str:
    return _NORM.sub("", (name or "").strip().strip('"').casefold())


def _set(*names: str) -> frozenset[str]:
    return frozenset(norm(n) for n in names)


COLUMNS: dict[str, frozenset[str]] = {
    "date": _set("date", "day", "usage_date", "usagedate", "usage_start_date", "usagestartdate",
                 "line_item_usage_start_date", "lineItem/UsageStartDate", "timestamp", "time_period", "start",
                 "charge_period_start", "chargeperiodstart"),
    "service": _set("service", "product", "service_name", "servicename", "product_name", "product/ProductName",
                    "line_item_product_code", "lineItem/ProductCode", "product_code", "productcode"),
    "cost": _set("cost", "unblended_cost", "unblendedcost", "amount", "line_item_unblended_cost",
                 "lineItem/UnblendedCost", "cost_usd", "total_cost", "billed_cost", "billedcost"),
    "account": _set("account", "account_id", "linked_account", "linkedaccount", "line_item_usage_account_id",
                    "lineItem/UsageAccountId", "project_id", "subscription_id"),
    "region": _set("region", "location", "product_region", "product/region"),
    "usage_type": _set("usage_type", "usagetype", "line_item_usage_type", "lineItem/UsageType", "sku"),
    "resource_id": _set("resource_id", "resourceid", "line_item_resource_id", "lineItem/ResourceId"),
    "currency": _set("currency", "currency_code", "line_item_currency_code", "lineItem/CurrencyCode",
                     "billing_currency"),
}
REQUIRED = ("date", "service", "cost")
DIMENSIONS = ("usage_type", "region", "account")
TAG_KEYS = ("team", "owner", "service", "project", "cost_center")
_TAG_PREFIXES = ("resourcetagsuser", "resourcetags", "tags", "tag", "labels", "label", "user")
_TAG_ALIASES = {"team": "team", "owner": "owner", "service": "service", "project": "project",
                "costcenter": "cost_center", "costcentre": "cost_center"}


@dataclass(frozen=True)
class Row:
    day: date
    service: str
    cost_micros: int
    currency: str
    account: str = ""
    region: str = ""
    usage_type: str = ""
    resource_id: str = ""
    tags: Mapping[str, str] = field(default_factory=dict)

    def dim(self, name: str) -> str:
        return getattr(self, name)


@dataclass(frozen=True)
class Export:
    rows: tuple[Row, ...]
    bad_rows: int
    data_rows: int
    tag_keys: tuple[str, ...]
    dimensions: tuple[str, ...]
    currency_given: bool
    read_chars: int
    total_chars: int

    @property
    def cut(self) -> bool:
        return self.read_chars < self.total_chars

    def totals(self) -> dict[str, int]:
        """sum(cost) per currency, in micro-units."""
        return _sum_by_currency(self.rows)

    def credits(self) -> dict[str, int]:
        """Negative rows (credits, refunds) per currency; kept in the totals, shown separately."""
        return _sum_by_currency(r for r in self.rows if r.cost_micros < 0)


def _sum_by_currency(rows) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:  # a local accumulator; the input is never changed
        out[r.currency] = out.get(r.currency, 0) + r.cost_micros
    return out


@dataclass(frozen=True)
class ParseError:
    message: str


@dataclass(frozen=True)
class Split:
    instruction: str
    table: str


def split(text: str) -> Split:
    """(instruction, table text) from one message; table is empty when no export is in it."""
    body = (text or "").replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = body.split("\n")
    at = find_header(lines)
    if at is None:
        return Split(body.strip()[:INSTRUCTION_MAX_CHARS], "")
    instruction = "\n".join(x for x in lines[:at] if not _FENCE_LINE.match(x)).strip().rstrip(":").strip()
    table = "\n".join(x for x in lines[at:] if x.strip() and not _FENCE_LINE.match(x))
    return Split(instruction[:INSTRUCTION_MAX_CHARS], table)


def delimiter_of(line: str) -> str:
    return max(_DELIMITERS, key=line.count)


def _width(line: str, delimiter: str) -> int:
    return len(next(csv.reader([line], delimiter=delimiter), []))


def find_header(lines: Sequence[str]) -> int | None:
    """The header line: the first with the required columns, else the first table-shaped line (so a table with a
    renamed column gets a clear "missing column" answer instead of being read as prose)."""
    strict = _find_header(lines, required=True)
    return strict if strict is not None else _find_header(lines, required=False)


def _find_header(lines: Sequence[str], required: bool) -> int | None:
    for i, line in enumerate(lines[:60]):
        if not line.strip() or _FENCE_LINE.match(line):
            continue
        delimiter = delimiter_of(line)
        width = _width(line, delimiter)
        if width < MIN_FIELDS:
            continue
        nxt = next((x for x in lines[i + 1:] if x.strip() and not _FENCE_LINE.match(x)), None)
        if nxt is not None and _width(nxt, delimiter) == width and (not required or _has_required(line, delimiter)):
            return i
    return None


def _has_required(line: str, delimiter: str) -> bool:
    headers = next(csv.reader([line], delimiter=delimiter), [])
    return bool(_map_headers(headers)) and all(r in _map_headers(headers) for r in REQUIRED)


def tag_key(header: str) -> str | None:
    """`tag_team`, `resourceTags/user:Team`, `tags.cost-center` → the canonical tag key, else None."""
    n = norm(header)
    for prefix in _TAG_PREFIXES:
        if n.startswith(prefix) and n[len(prefix):] in _TAG_ALIASES:
            return _TAG_ALIASES[n[len(prefix):]]
    return None


def _map_headers(headers: Sequence[str]) -> dict[str, int]:
    """role → column index; the first header matching a role wins. Tag columns are mapped separately."""
    mapped: dict[str, int] = {}
    for i, h in enumerate(headers):
        n = norm(h)
        role = next((r for r, names in COLUMNS.items() if n in names and r not in mapped), None)
        if role is not None and tag_key(h) is None:
            mapped = {**mapped, role: i}
    return mapped


def _tag_columns(headers: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, h in enumerate(headers):
        key = tag_key(h)
        if key is not None and key not in out:
            out = {**out, key: i}
    return out


def parse(table: str) -> Export | ParseError:
    total = len(table)
    text = _cut(table)
    lines = text.split("\n")
    delimiter = delimiter_of(lines[0])
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    headers = [h.strip() for h in next(reader, [])]
    mapped = _map_headers(headers)
    missing = [r for r in REQUIRED if r not in mapped]
    if missing:
        return ParseError(_missing_columns(missing, headers))
    tags = _tag_columns(headers)
    parsed = [_row(raw, mapped, tags) for raw in reader if any(c.strip() for c in raw)]
    rows = tuple(r for r in parsed if r is not None)
    bad = len(parsed) - len(rows)
    data_rows = len(parsed)
    dims = tuple(d for d in DIMENSIONS if d in mapped)
    keys = tuple(k for k in TAG_KEYS if k in tags)
    return Export(rows, bad, data_rows, keys, dims, "currency" in mapped, len(text), total)


def _cut(table: str) -> str:
    """At most MAX_PASTE_CHARS, cut at a line end; what was read is said in the reply."""
    if len(table) <= MAX_PASTE_CHARS:
        return table
    head = table[:MAX_PASTE_CHARS]
    return head[: head.rfind("\n")] if "\n" in head else head


def _missing_columns(missing: Sequence[str], headers: Sequence[str]) -> str:
    seen = ", ".join(h for h in headers if h) or "none"
    names = ", ".join(f"`{m}`" for m in missing)
    return (f"I couldn't find the {names} column(s) in your export (headers I saw: {seen}). Rename them to "
            "`date`, `service` and `cost` (plus optional `usage_type`, `region`, `account` and tag columns such "
            "as `tag_team`) and paste it again.")


def _cell(raw: Sequence[str], index: int | None) -> str:
    if index is None or index >= len(raw):
        return ""
    return raw[index].strip()


def _row(raw: Sequence[str], mapped: Mapping[str, int], tags: Mapping[str, int]) -> Row | None:
    day = parse_day(_cell(raw, mapped["date"]))
    cost = parse_micros(_cell(raw, mapped["cost"]))
    service = _cell(raw, mapped["service"])
    if day is None or cost is None or not service:
        return None
    currency = _cell(raw, mapped.get("currency")).upper() or DEFAULT_CURRENCY
    return Row(day=day, service=service, cost_micros=cost, currency=currency,
               account=_cell(raw, mapped.get("account")), region=_cell(raw, mapped.get("region")),
               usage_type=_cell(raw, mapped.get("usage_type")), resource_id=_cell(raw, mapped.get("resource_id")),
               tags={k: _cell(raw, i) for k, i in tags.items() if _cell(raw, i)})


def parse_day(value: str) -> date | None:
    """`YYYY-MM-DD`, or an ISO timestamp (converted to its UTC date)."""
    text = value.strip().strip('"')
    if _DATE_PREFIX.match(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    try:
        stamp = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        return None
    return stamp.astimezone(UTC).date() if stamp.tzinfo else stamp.date()


def parse_micros(value: str) -> int | None:
    """`1,234.567891` → 1234567891; `$-3.50` → -3500000; anything not numeric → None."""
    text = _MONEY_JUNK.sub("", value.strip().strip('"'))
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    return int((amount * MICROS).quantize(Decimal(1), rounding=ROUND_HALF_UP))
