"""The code post-check: the guarantee, not the prompt.

Every id, amount, address, quote and link the model returns must appear in this turn's tool output
(`seen`), or the record (or that field) is dropped. A complaint is a sentence copied verbatim from an
inbound email; an inbound email counts only when it came from the account's own address (or its
company domain, never a shared consumer domain)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from freemail import FREE_MAIL_DOMAINS
from records import Change, Failure, Inbound, Item, MailRecord, Sub, Ticket

_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
STATUSES = frozenset({"active", "trialing", "past_due", "canceled", "unpaid", "incomplete",
                      "incomplete_expired", "paused"})


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a sentence says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str | None, seen: str) -> bool:
    """`needle` (a quote, link or id) occurs in normalised tool output. Empty never matches."""
    wanted = normalise_text(needle or "").strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def number_appears(value: int, seen: str) -> bool:
    return re.search(rf"(?<![\d.]){value}(?![\d])", seen) is not None


def _keep(value: str, seen: str) -> str:
    return value if appears_in(value, seen) else ""


def _items_backed(items: Iterable[Item], seen: str) -> bool:
    return all(i.unit_amount_minor is None or number_appears(i.unit_amount_minor, seen) for i in items)


@dataclass(frozen=True)
class Checked:
    subs: tuple[Sub, ...]
    dropped: int = 0


def check_subs(subs: Sequence[Sub], seen_text: str) -> Checked:
    seen = normalise_text(seen_text)
    kept: dict[str, Sub] = {}
    for sub in subs:
        backed = (appears_in(sub.id, seen) and appears_in(sub.customer_id, seen) and sub.status in STATUSES
                  and len(sub.currency) == 3 and _items_backed(sub.items, seen))
        if backed and sub.id not in kept:
            kept = {**kept, sub.id: replace(
                sub, customer_email=_keep(sub.customer_email, seen) if _EMAIL.fullmatch(sub.customer_email) else "",
                customer_name=_keep(sub.customer_name, seen), link=_keep(sub.link, seen),
                cancellation_reason=_keep(sub.cancellation_reason, seen),
                cancellation_comment=_keep(sub.cancellation_comment, seen))}
    return Checked(tuple(kept.values()), len(subs) - len(kept))


def check_changes(changes: Sequence[Change], failures: Sequence[Failure], subs: Sequence[Sub],
                  seen_text: str) -> tuple[tuple[Change, ...], tuple[Failure, ...], int]:
    seen = normalise_text(seen_text)
    known = {s.id for s in subs}
    customers = {s.customer_id for s in subs}
    ok_changes = tuple(c for c in changes if c.subscription_id in known and appears_in(c.subscription_id, seen)
                       and _items_backed((*c.previous_items, *c.items), seen))
    ok_failures = tuple(f for f in failures if f.customer_id in customers and appears_in(f.invoice_id, seen)
                        and appears_in(f.customer_id, seen))
    return ok_changes, ok_failures, len(changes) + len(failures) - len(ok_changes) - len(ok_failures)


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].casefold() if "@" in address else ""


def from_account(sender: str, account_email: str) -> bool:
    """The sender is the account's own address, or shares its company (non-consumer) domain."""
    found = _EMAIL.findall(sender or "")
    if not found or not account_email:
        return False
    address = found[0].casefold()
    if address == account_email.casefold():
        return True
    domain = domain_of(account_email)
    return bool(domain) and domain not in FREE_MAIL_DOMAINS and domain_of(address) == domain


def check_mail(records: Sequence[MailRecord], emails: Mapping[str, str], seen_text: str) -> dict[str, MailRecord]:
    """Mail records by account id, for the accounts that were asked about only."""
    seen = normalise_text(seen_text)
    out: dict[str, MailRecord] = {}
    for record in records:
        email = emails.get(record.account_id)
        if email is None or record.account_id in out:
            continue
        inbound = tuple(_inbound(i, seen) for i in record.inbound
                        if appears_in(i.link, seen) and from_account(i.sender, email))
        out = {**out, record.account_id: replace(record, inbound=inbound)}
    return out


def _inbound(item: Inbound, seen: str) -> Inbound:
    return replace(item, quote=item.quote if appears_in(item.quote, seen) else None,
                   subject=_keep(item.subject, seen), thread_id=_keep(item.thread_id, seen))


def check_tickets(tickets: Sequence[Ticket], account_ids: Iterable[str], seen_text: str) -> dict[str, tuple]:
    seen = normalise_text(seen_text)
    wanted = set(account_ids)
    out: dict[str, tuple[Ticket, ...]] = {a: () for a in wanted}
    for t in tickets:
        if t.account_id in wanted and appears_in(t.id, seen) and t.id not in {x.id for x in out[t.account_id]}:
            out = {**out, t.account_id: (*out[t.account_id], replace(t, subject=_keep(t.subject, seen),
                                                                      link=_keep(t.link, seen)))}
    return out
