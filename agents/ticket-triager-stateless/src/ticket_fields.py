"""Pure: field-name aliases shared by every ticket format, and one ticket built from named fields."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping

import tiers
from model import Ticket

ALIASES = {
    "id": ("id", "ticket", "ticket id", "ticket_id", "ticket #", "ticket no", "key", "#", "number"),
    "subject": ("subject", "title", "summary"),
    "body": ("body", "description", "message", "text", "first message", "first_message", "comment", "details",
             "content"),
    "customer": ("customer", "requester", "account", "company", "from", "organization", "org"),
    "tier": ("tier", "plan", "segment", "priority_tier", "priority tier", "customer tier"),
    "created": ("created", "created_at", "created at", "opened", "opened_at", "date"),
    "updated": ("updated", "updated_at", "updated at"),
    "status": ("status", "state"),
    "channel": ("channel", "source", "via"),
    "contacts": ("contacts", "replies", "reopen_count", "reopens", "responses", "contact count"),
    "assignee": ("assignee", "assigned to", "assigned_to", "owner", "agent"),
}
_LOOKUP = {alias: key for key, names in ALIASES.items() for alias in names}
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t]+")
_DIGITS = re.compile(r"\d+")
_ID_CLEAN = re.compile(r"[^\w.-]")
SUBJECT_MAX = 200


def canonical(name: str) -> str | None:
    """The field a header/label/key names, or None when we don't read it."""
    return _LOOKUP.get(" ".join(str(name).strip().strip("*").casefold().split()))


def clean_text(value: object) -> str:
    """HTML-stripped, entity-decoded text; ticket exports often carry HTML bodies."""
    text = html.unescape(_TAG.sub(" ", str(value or "")))
    return "\n".join(_SPACES.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")).strip()


def build(values: Mapping[str, object], fallback_id: str) -> Ticket | None:
    """A ticket from canonical-keyed values; None when it has neither subject nor body."""
    subject = clean_text(values.get("subject"))[:SUBJECT_MAX]
    body = clean_text(values.get("body"))
    if not subject and not body:
        return None
    raw_id = _ID_CLEAN.sub("", str(values.get("id") or "").strip().lstrip("#"))
    contacts = _DIGITS.search(str(values.get("contacts") or ""))
    return Ticket(
        id=raw_id or fallback_id, subject=subject, body=body, customer=_opt(values.get("customer")),
        tier=tiers.normalise(_opt(values.get("tier"))), created=_opt(values.get("created")),
        status=_opt(values.get("status")), channel=_opt(values.get("channel")),
        assignee=_opt(values.get("assignee")), contacts=int(contacts.group()) if contacts else 0,
        generated_id=not raw_id,
    )


def _opt(value: object) -> str | None:
    text = clean_text(value)
    return text or None
