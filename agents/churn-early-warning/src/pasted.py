"""Pure: pasted billing rows, the fallback when no payments source is connected.

    account, email, mrr, renewal_date, status, changed_at, change

One row per account, MRR in major units (a 3-letter currency code may follow the number). `status` is
active / trialing / past_due / canceled / cancel_scheduled; `change` is free words such as
"downgrade 40%", "seats 10 -> 8", "2 failed payments" or "cancel at period end". Rows are read in code;
the same signal rules apply. Email and the support desk are not checked for pasted rows."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import mrr
from records import parse_day
from signals import (
    DESK,
    EMAIL,
    RENEWAL_SOON_DAYS,
    TRIAL_DAYS,
    Account,
    Signal,
    signal,
    strongest,
)

COLUMNS = ("account", "email", "mrr", "renewal_date", "status", "changed_at", "change")
MAX_ROWS = 500
_DELIMITERS = ",\t|;"
_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)")
_CODE = re.compile(r"\b([A-Za-z]{3})\b")
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_SEATS = re.compile(r"(\d+)\s*(?:->|→|to)\s*(\d+)")
_FAILED = re.compile(r"(?:(\d+)\s*(?:x\s*)?(?:failed|failures?|attempts?))|(?:fail\w*\s*(?:x|×)\s*(\d+))",
                     re.IGNORECASE)


@dataclass(frozen=True)
class Pasted:
    accounts: tuple[Account, ...]
    skipped: int = 0   # rows without a readable account name or MRR


def find_rows(text: str) -> list[dict[str, str]] | None:
    """The rows under a header line naming the columns, or None when the message holds no such table."""
    lines = (text or "").splitlines()
    for n, line in enumerate(lines):
        delimiter = next((d for d in _DELIMITERS if d in line), None)
        if delimiter is None:
            continue
        header = [h.strip().strip("|").strip().lower() for h in next(csv.reader([line], delimiter=delimiter))]
        if {"account", "mrr", "status"} <= set(header):
            body = "\n".join(lines[n + 1:])
            rows = csv.DictReader(io.StringIO(body), fieldnames=header, delimiter=delimiter, skipinitialspace=True)
            return [{k: (v or "").strip() for k, v in r.items() if isinstance(k, str)} for r in rows
                    if any((v or "").strip(" -|") for v in r.values() if isinstance(v, str))][:MAX_ROWS]
    return None


def _money(cell: str) -> tuple[Decimal, str] | None:
    amount = _AMOUNT.search(cell or "")
    if not amount:
        return None
    try:
        value = Decimal(amount.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    code = _CODE.search(cell.replace(amount.group(1), " "))
    return value, code.group(1).upper() if code else ""


def accounts(rows: Sequence[Mapping[str, str]], today: date, lookahead: int, lookback: int) -> Pasted:
    out: list[Account] = []
    skipped = 0
    for row in rows:
        name, money = row.get("account", ""), _money(row.get("mrr", ""))
        if not name or money is None:
            skipped += 1
            continue
        out.append(_account(row, name, money, today, lookahead, lookback))
    return Pasted(tuple(out), skipped)


def _account(row: Mapping[str, str], name: str, money: tuple[Decimal, str], today: date, lookahead: int,
             lookback: int) -> Account:
    value, currency = money
    renews = parse_day(row.get("renewal_date"))
    email = row.get("email", "") if _EMAIL.match(row.get("email", "")) else ""
    found = _signals(row, renews, today, lookahead, lookback)
    future = renews.isoformat() if renews is not None and renews >= today else None
    return Account(id=name, name=name, email=email, currency=currency, mrr_minor=mrr.to_minor(value, currency),
                   signals=strongest(found), not_checked=(EMAIL, DESK), effective_dates=(future,) if future else ())


def _signals(row: Mapping[str, str], renews: date | None, today: date, lookahead: int,
             lookback: int) -> list[Signal]:
    status = row.get("status", "").strip().lower().replace("-", "_").replace(" ", "_")
    change = row.get("change", "").strip().lower()
    changed = parse_day(row.get("changed_at"))
    recent = changed is None or today - timedelta(days=lookback) <= changed <= today
    days = (renews - today).days if renews else None
    out: list[Signal] = []
    if status in ("canceled", "cancelled"):
        if changed is not None and recent:
            out.append(signal("canceled", f"cancelled on {changed.isoformat()}"))
        return out
    scheduled = status in ("cancel_scheduled", "canceling", "cancelling") or "cancel" in change
    if scheduled and days is not None and days >= 0:
        out.append(signal("cancel_scheduled", f"set to cancel on {renews.isoformat()}"))
    if status == "past_due":
        out.append(signal("past_due", "status past_due"))
    if recent:
        out.extend(_change_signals(change, changed))
    if not scheduled and status in ("active", "trialing") and days is not None and 0 <= days <= lookahead:
        weight = 4 if days <= RENEWAL_SOON_DAYS else 3
        out.append(signal("renewal_near", f"renews {renews.isoformat()} (in {days} days)", weight=weight))
    if status == "trialing" and days is not None and 0 <= days <= TRIAL_DAYS:
        out.append(signal("trial_ending", f"trial ends {renews.isoformat()} (in {days} days)"))
    return out


def _change_signals(change: str, changed: date | None) -> list[Signal]:
    when = f" on {changed.isoformat()}" if changed else ""
    out: list[Signal] = []
    if "downgrade" in change:
        pct = _PERCENT.search(change)
        weight = 7 if pct and Decimal(pct.group(1)) >= 30 else 6
        out.append(signal("downgrade", f"downgrade{f' -{pct.group(1)}%' if pct else ''}{when}", weight=weight))
    seats = _SEATS.search(change)
    if "seat" in change and (seats is None or int(seats.group(2)) < int(seats.group(1))):
        detail = f" {seats.group(1)} → {seats.group(2)}" if seats else ""
        out.append(signal("seat_reduction", f"seats{detail}{when}"))
    failed = _FAILED.search(change) if "fail" in change else None
    count = int(failed.group(1) or failed.group(2)) if failed else 0
    if count >= 2:
        out.append(signal("payment_failed_repeatedly", f"{count} failed payments{when}"))
    return out
