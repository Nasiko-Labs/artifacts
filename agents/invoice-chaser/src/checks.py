"""The code post-checks: the guarantee, not the prompt.

An invoice survives only when its id or number, its amount and its due date all appear in this turn's
payments tool output, the amount is a positive integer, the status is open and the due date is before
today. A reminder survives only with a verbatim link, a send time on or after `due date - 7 days`, and
verbatim evidence that names the invoice number or link. A customer reply needs a verbatim link, the
customer's own address and a time after the last reminder."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from invoices import Entry, Invoice, Reminder, Reply

REMINDER_LOOKBACK_DAYS = 7
_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True)
class CheckedInvoices:
    invoices: tuple[Invoice, ...]
    dropped: int = 0      # unbacked, not open, or non-positive amount
    no_due_date: int = 0  # auto-charge invoices without a due date: counted, not listed
    not_due: int = 0      # open but not yet past due: expected, not a doubt about the data


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a value says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str | None, seen: str) -> bool:
    """`needle` (an id, number, link or quote) occurs in normalised tool output. Empty never matches."""
    wanted = normalise_text(needle or "").strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def parse_time(value: str | None) -> datetime | None:
    """An ISO date/datetime or epoch seconds, as an aware UTC datetime."""
    text = (value or "").strip()
    epoch = _EPOCH.match(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_day(value: str | None) -> date | None:
    parsed = parse_time(value)
    return parsed.date() if parsed else None


def addresses_in(text: str) -> tuple[str, ...]:
    """Every email address in tool output, case-folded, de-duplicated in order."""
    return tuple(dict.fromkeys(e.casefold() for e in EMAIL.findall(text or "")))


# --- invoices -------------------------------------------------------------------------------------

def post_check_invoices(invoices: Iterable[Invoice], *, today: date, seen_text: str,
                        trusted: bool = False) -> CheckedInvoices:
    """`trusted` is for rows the user pasted: they are the source, so only the date and amount rules apply."""
    seen = normalise_text(seen_text)
    kept: dict[str, Invoice] = {}
    dropped = no_due = not_due = 0
    for invoice in invoices:
        if invoice.auto_charge and not invoice.due_date:
            no_due += 1
            continue
        due = parse_day(invoice.due_date)
        if due is not None and due >= today:
            not_due += 1
            continue
        checked = _checked_invoice(invoice, today, seen, trusted)
        if checked is None:
            dropped += 1
        elif checked.id not in kept:
            kept = {**kept, checked.id: checked}
        else:
            dropped += 1
    return CheckedInvoices(tuple(kept.values()), dropped, no_due, not_due)


def _checked_invoice(invoice: Invoice, today: date, seen: str, trusted: bool) -> Invoice | None:
    due = parse_day(invoice.due_date)
    if due is None or due >= today or invoice.amount_due_minor <= 0 or not invoice.currency:
        return None
    if not trusted and not _on_record(invoice, seen):
        return None
    email = invoice.customer_email if trusted or appears_in(invoice.customer_email, seen) else None
    name = invoice.customer_name if trusted or appears_in(invoice.customer_name, seen) else None
    url = invoice.hosted_url if appears_in(invoice.hosted_url, seen) else None
    link = invoice.link if appears_in(invoice.link, seen) else None
    return replace(invoice, id=invoice.id or invoice.number, customer_email=email, customer_name=name,
                   hosted_url=url, link=link, due=due.isoformat())


def _on_record(invoice: Invoice, seen: str) -> bool:
    named = appears_in(invoice.id, seen) or appears_in(invoice.number, seen)
    amount = re.search(rf"(?<![\d.]){invoice.amount_due_minor}(?![\d])", seen) is not None
    is_open = invoice.status == "open" and "open" in seen
    return named and amount and appears_in(invoice.due_date, seen) and is_open


def check_customers(invoices: Sequence[Invoice], found: Mapping[str, tuple[str | None, str | None]],
                    seen_text: str) -> tuple[Invoice, ...]:
    """Fill a missing email/name only from a customer record whose id and value were both returned."""
    seen = normalise_text(seen_text)
    filled = []
    for invoice in invoices:
        email, name = found.get(invoice.customer_id, (None, None))
        if not appears_in(invoice.customer_id, seen):
            filled.append(invoice)
            continue
        filled.append(replace(
            invoice,
            customer_email=invoice.customer_email or (email if appears_in(email, seen) and EMAIL.fullmatch(email or "")
                                                      else None),
            customer_name=invoice.customer_name or (name if appears_in(name, seen) else None)))
    return tuple(filled)


# --- reminders and replies ------------------------------------------------------------------------

def attach_history(entries: Sequence[Entry], reminders: Iterable[Reminder], replies: Iterable[Reply], *,
                   now: datetime, seen_text: str) -> tuple[Entry, ...]:
    """Every entry is marked checked; each keeps only verified reminders (by link) and its latest
    verified customer reply after the last reminder (or after the due date with none)."""
    seen = normalise_text(seen_text)
    reminders, replies = tuple(reminders), tuple(replies)
    result = []
    for entry in entries:
        own = _verified_reminders(entry.invoice, [r for r in reminders if r.invoice_id == entry.invoice.id], seen, now)
        reply = _verified_reply(entry.invoice, own, [r for r in replies if r.invoice_id == entry.invoice.id], seen, now)
        result.append(replace(entry, reminders=own, reminders_checked=True, reply=reply))
    return tuple(result)


def _verified_reminders(invoice: Invoice, candidates: Sequence[Reminder], seen: str,
                        now: datetime) -> tuple[Reminder, ...]:
    earliest = invoice.due_day - timedelta(days=REMINDER_LOOKBACK_DAYS)
    by_link: dict[str, Reminder] = {}
    for r in candidates:
        sent = parse_time(r.sent_at)
        if sent is None or not (earliest <= sent.date() and sent <= now):
            continue
        if not (appears_in(r.link, seen) and appears_in(r.evidence, seen) and _names_invoice(r.evidence, invoice)):
            continue
        cleaned = replace(r, subject=r.subject if appears_in(r.subject, seen) else "",
                          thread_ref=r.thread_ref if appears_in(r.thread_ref, seen) else "")
        by_link.setdefault(normalise_text(r.link), cleaned)
    return tuple(sorted(by_link.values(), key=lambda r: parse_time(r.sent_at)))


def _names_invoice(evidence: str, invoice: Invoice) -> bool:
    text = normalise_text(evidence)
    return any(appears_in(v, text) for v in (invoice.number, invoice.hosted_url) if v)


def _verified_reply(invoice: Invoice, reminders: Sequence[Reminder], candidates: Sequence[Reply], seen: str,
                    now: datetime) -> Reply | None:
    last = parse_time(reminders[-1].sent_at) if reminders else datetime.combine(invoice.due_day, datetime.min.time(),
                                                                                 tzinfo=UTC)
    customer = (invoice.customer_email or "").casefold()
    good = []
    for r in candidates:
        at = parse_time(r.received_at)
        sender = {e.casefold() for e in EMAIL.findall(r.sender)}
        if at is None or not (last < at <= now) or not customer or customer not in sender:
            continue
        if appears_in(r.link, seen):
            good.append(replace(r, subject=r.subject if appears_in(r.subject, seen) else ""))
    return max(good, key=lambda r: parse_time(r.received_at)) if good else None


def reminders_after_due(entry: Entry) -> tuple[Reminder, ...]:
    due = entry.invoice.due_day
    return tuple(r for r in entry.reminders if (parse_day(r.sent_at) or date.min) > due)
