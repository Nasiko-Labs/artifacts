"""The code post-check: the guarantee, not the prompt.

A promise survives only when its quote and its link both appear in this turn's tool output and it
was sent inside the window. `done` needs evidence a tool returned; otherwise it is `unclear`. Due
dates are kept only when on or after the send date, and overdue days are computed here."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from promises import Promise, Verdict, with_id

MAX_ITEMS = 15
_ISO_DATE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})")
_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"


@dataclass(frozen=True)
class Ledger:
    """What the user is shown: open then unclear, numbered from 1, at most MAX_ITEMS."""

    items: tuple[Promise, ...]
    done: int
    capped: int


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a sentence says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str, seen: str) -> bool:
    """`needle` (a quote, link or id) occurs in normalised tool output. Empty never matches."""
    wanted = normalise_text(needle).strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def parse_day(value: str | None) -> date | None:
    """An ISO date/datetime or epoch seconds (chat timestamps), as a UTC date."""
    text = value or ""
    epoch = _EPOCH.match(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC).date()
    match = _ISO_DATE.match(text)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def post_check(
    candidates: Iterable[Promise], *, today: date, window_days: int, seen_text: str
) -> tuple[Promise, ...]:
    """Candidates whose quote and link a tool returned, sent inside the window; one per (link, quote)."""
    seen = normalise_text(seen_text)
    since = today - timedelta(days=window_days)
    kept: tuple[Promise, ...] = ()
    keys: frozenset[tuple[str, str]] = frozenset()
    for promise in candidates:
        key = (normalise_text(promise.link), normalise_text(promise.quote))
        if key in keys or not _is_backed(promise, seen, since, today):
            continue
        kept = (*kept, replace(promise, due_date=_valid_due(promise)))
        keys = keys | {key}
    return kept


def _is_backed(promise: Promise, seen: str, since: date, today: date) -> bool:
    sent = parse_day(promise.sent_at)
    in_window = sent is not None and since <= sent <= today
    return in_window and appears_in(promise.quote, seen) and appears_in(promise.link, seen)


def _valid_due(promise: Promise) -> str | None:
    due, sent = parse_day(promise.due_date), parse_day(promise.sent_at)
    if due is None or sent is None or due < sent:
        return None
    return due.isoformat()


def apply_verdicts(
    promises: Sequence[Promise], verdicts: Mapping[str, Verdict], seen_text: str
) -> tuple[Promise, ...]:
    """`done` only with evidence a tool returned this turn, else `unclear`; no verdict → `open`."""
    seen = normalise_text(seen_text)
    return tuple(_judged(p, verdicts.get(p.id), seen) for p in promises)


def _judged(promise: Promise, verdict: Verdict | None, seen: str) -> Promise:
    if verdict is None:
        return replace(promise, state="open", evidence_link=None)
    if verdict.state == "done":
        backed = verdict.evidence_link is not None and appears_in(verdict.evidence_link, seen)
        return replace(promise, state="done" if backed else "unclear", evidence_link=verdict.evidence_link)
    return replace(promise, state=verdict.state, evidence_link=verdict.evidence_link)


def overdue_days(promise: Promise, today: date) -> int | None:
    """today − due, in days (negative when not yet due); None when there is no due date."""
    due = parse_day(promise.due_date)
    return None if due is None else (today - due).days


def ledger(promises: Sequence[Promise], today: date) -> Ledger:
    """Open first, then unclear; each by most overdue, then oldest sent. Renumbered 1..n, capped."""
    open_items = _sorted([p for p in promises if p.state == "open"], today)
    unclear = _sorted([p for p in promises if p.state == "unclear"], today)
    ordered = (*open_items, *unclear)
    shown = tuple(with_id(p, str(n)) for n, p in enumerate(ordered[:MAX_ITEMS], start=1))
    done = sum(1 for p in promises if p.state == "done")
    return Ledger(items=shown, done=done, capped=max(0, len(ordered) - MAX_ITEMS))


def _sorted(promises: list[Promise], today: date) -> list[Promise]:
    def key(p: Promise) -> tuple:
        overdue = overdue_days(p, today)
        sent = parse_day(p.sent_at) or date.max
        return (overdue is None, -(overdue or 0), sent, p.sent_at)

    return sorted(promises, key=key)
