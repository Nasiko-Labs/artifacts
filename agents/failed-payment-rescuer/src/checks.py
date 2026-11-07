"""The code post-check: the guarantee, not the prompt.

A failure survives only when its id and the customer's email appear in this turn's tool output, its
amount is a positive integer found in that output, and it failed inside the window. Optional fields
(invoice, name, decline code, message, plan, link) are kept only when verbatim. History counts are
recomputed from the payment rows whose ids are verbatim; a prior contact counts only with a verbatim
link inside the last three days."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from failures import Contact, CustomerHistory, Failure, PaymentRow, Request

MAX_FAILURES = 40
MAX_ENRICHED_CUSTOMERS = 15
CONTACT_WINDOW_DAYS = 3
SUCCEEDED = frozenset({"paid", "succeeded", "success", "successful"})
FAILED = frozenset({"failed", "open", "uncollectible", "past_due", "requires_payment_method"})
_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LIVEMODE = re.compile(r"[\"']?livemode[\"']?\s*[:=]\s*(true|false)", re.IGNORECASE)


@dataclass(frozen=True)
class Checked:
    failures: tuple[Failure, ...]
    dropped: int = 0      # unbacked, outside the window, or malformed
    filtered: int = 0     # left out by the user's own filters (amount, currency, customer)
    capped: int = 0       # over MAX_FAILURES: left out


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a value says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str | None, seen: str) -> bool:
    """`needle` (an id, address, link or quote) occurs in normalised tool output. Empty never matches."""
    wanted = normalise_text(needle or "").strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def number_in(value: int | None, raw: str) -> bool:
    """The integer occurs in the raw output as a whole number (not inside a longer number)."""
    if value is None or value <= 0:
        return False
    return re.search(rf"(?<![\d.]){value}(?![\d])", raw or "") is not None


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


def livemode(seen_text: str) -> bool | None:
    """From the tool output itself: False when every record says test mode, None when none says."""
    flags = {m.group(1).lower() for m in _LIVEMODE.finditer(seen_text or "")}
    if not flags:
        return None
    return "true" in flags


def post_check(failures: Iterable[Failure], *, now: datetime, request: Request, seen_text: str,
               pasted: bool = False) -> Checked:
    """Pasted rows carry no tool ids and are the user's own choice of rows: no id or window check."""
    seen = normalise_text(seen_text)
    since = now.date() - timedelta(days=request.window_days)
    backed, dropped, filtered = [], 0, 0
    for failure in failures:
        failed = parse_time(failure.failed_at)
        in_window = pasted or since <= failed.date() <= now.date() if failed else False
        if failed is None or not in_window or not _is_backed(failure, seen, seen_text, pasted):
            dropped += 1
        elif not _wanted(failure, request):
            filtered += 1
        else:
            backed.append(_cleaned(failure, seen))
    unique = _dedupe(backed)
    shown = unique[:MAX_FAILURES]
    keyed = tuple(replace(f, key=f"f{n}", number=str(n)) for n, f in enumerate(shown, start=1))
    return Checked(keyed, dropped, filtered, len(unique) - len(shown))


def _is_backed(failure: Failure, seen: str, raw: str, pasted: bool = False) -> bool:
    if pasted:
        return EMAIL.fullmatch(failure.customer_email) is not None and failure.amount_minor > 0
    return (EMAIL.fullmatch(failure.customer_email) is not None and appears_in(failure.id, seen)
            and appears_in(failure.customer_email, seen) and bool(failure.customer_id)
            and number_in(failure.amount_minor, raw) and len(failure.currency) == 3)


def _wanted(failure: Failure, request: Request) -> bool:
    if request.currency and failure.currency != request.currency:
        return False
    if request.min_amount_minor is not None and failure.amount_minor < request.min_amount_minor:
        return False
    if request.customers:
        names = {c.casefold() for c in request.customers}
        mine = {failure.customer_email.casefold(), (failure.customer_name or "").casefold(),
                failure.customer_id.casefold()}
        return bool(names & mine)
    return True


def _cleaned(failure: Failure, seen: str) -> Failure:
    def kept(value: str | None) -> str | None:
        return value if appears_in(value, seen) else None

    attempts = failure.attempt_count if failure.attempt_count and failure.attempt_count > 0 else None
    return replace(failure, invoice_id=kept(failure.invoice_id), customer_name=kept(failure.customer_name),
                   decline_code=kept(failure.decline_code), failure_message=kept(failure.failure_message),
                   plan_name=kept(failure.plan_name), link=kept(failure.link), attempt_count=attempts,
                   next_attempt_at=failure.next_attempt_at if parse_time(failure.next_attempt_at) else None)


def _dedupe(failures: Sequence[Failure]) -> list[Failure]:
    """One failure per invoice (else per customer + amount + day): several attempts on one invoice become
    one failure with the highest attempt count seen and the latest attempt's details. Oldest first."""
    ordered = sorted(failures, key=lambda f: parse_time(f.failed_at) or datetime.max.replace(tzinfo=UTC))
    kept: dict[tuple, Failure] = {}
    for failure in ordered:
        key = (("invoice", failure.invoice_id) if failure.invoice_id
               else ("charge", failure.customer_id, failure.amount_minor, parse_day(failure.failed_at)))
        earlier = kept.get(key)
        if earlier is None:
            kept = {**kept, key: failure}
            continue
        attempts = max(earlier.attempt_count or 1, failure.attempt_count or 1, 2)
        kept = {**kept, key: replace(failure, attempt_count=attempts, plan_name=failure.plan_name or earlier.plan_name,
                                     link=failure.link or earlier.link)}
    return list(kept.values())


# --- history -----------------------------------------------------------------------------------------

def apply_histories(failures: Sequence[Failure], histories: Sequence[CustomerHistory], seen_text: str,
                    monthly: Mapping[str, int | None]) -> tuple[Failure, ...]:
    """Merge each verified history into its customer's failures. `monthly` is the normalised monthly
    amount per customer id, computed by the caller (scoring.monthly_minor)."""
    seen = normalise_text(seen_text)
    by_customer = {h.customer_id: h for h in histories if appears_in(h.customer_id, seen)}
    return tuple(_with_history(f, by_customer.get(f.customer_id), seen, seen_text, monthly) for f in failures)


def verified_plan_amount(history: CustomerHistory, seen_text: str) -> int | None:
    return history.plan_amount_minor if number_in(history.plan_amount_minor, seen_text) else None


def _with_history(failure: Failure, history: CustomerHistory | None, seen: str, raw: str,
                  monthly: Mapping[str, int | None]) -> Failure:
    if history is None:
        return replace(failure, history_checked=True)
    own = {failure.id.casefold(), (failure.invoice_id or "").casefold()}
    rows = tuple(r for r in history.rows if appears_in(r.id, seen) and r.id.casefold() not in own)
    ok = [r for r in rows if r.status in SUCCEEDED]
    bad = [r for r in rows if r.status in FAILED]
    successful = len(ok) if rows else _count(history.successful_payments)
    prior = len(bad) if rows else _count(history.prior_failures)
    lifetime = sum(_amount(r, failure.currency, raw) for r in ok)
    disputes = _verified_count(history.dispute_ids, history.disputes, seen)
    refunds = _verified_count(history.refund_ids, history.refunds, seen)
    plan = failure.plan_name or (history.plan_name if appears_in(history.plan_name, seen) else None)
    return replace(failure, history_checked=True, successful_payments=successful, prior_failures=prior,
                   lifetime_paid_minor=lifetime, disputes=disputes, refunds=refunds, plan_name=plan,
                   monthly_minor=monthly.get(failure.customer_id))


def _count(value: int | None) -> int:
    return value if value is not None and value >= 0 else 0


def _amount(row: PaymentRow, currency: str, raw: str) -> int:
    same = not row.currency or row.currency == currency
    return row.amount_minor if same and number_in(row.amount_minor, raw) else 0


def _verified_count(ids: Sequence[str], count: int, seen: str) -> int:
    """With ids, only the verbatim ones count; without, the model's count stands (it can only add caution)."""
    return sum(1 for i in ids if appears_in(i, seen)) if ids else count


# --- prior contact -----------------------------------------------------------------------------------

def apply_contacts(failures: Sequence[Failure], contacts: Sequence[Contact], seen_text: str,
                   now: datetime) -> tuple[Failure, ...]:
    """Every failure is marked contact-checked; a contact counts only with a verbatim link, a sent time
    inside the last CONTACT_WINDOW_DAYS, and the failure's own address."""
    seen = normalise_text(seen_text)
    recent: dict[str, Contact] = {}
    for contact in contacts:
        sent = parse_time(contact.last_sent_at)
        fresh = sent is not None and now - timedelta(days=CONTACT_WINDOW_DAYS) <= sent <= now
        if fresh and appears_in(contact.link, seen):
            recent = {**recent, contact.customer_email.casefold(): contact}
    return tuple(_contacted(f, recent.get(f.customer_email.casefold())) for f in failures)


def _contacted(failure: Failure, contact: Contact | None) -> Failure:
    if contact is None:
        return replace(failure, contact_checked=True, contacted_at=None, contact_link=None)
    return replace(failure, contact_checked=True, contacted_at=contact.last_sent_at, contact_link=contact.link)
