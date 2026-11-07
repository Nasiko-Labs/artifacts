"""User-facing text: the promise table, the list given to the follow-up model, a draft to copy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from checks import Ledger, overdue_days
from promises import Promise

NO_DATE = "no date"
_OUTCOME_LABELS = {"sent": "reply sent", "skipped": "skipped", "denied": "send denied",
                   "blocked": "sending blocked", "not_sent": "not sent"}


def render_ledger(ledger: Ledger, *, today: date, window_days: int, dropped: int,
                  notes: Sequence[str] = ()) -> str:
    open_items = [p for p in ledger.items if p.state == "open"]
    unclear = [p for p in ledger.items if p.state == "unclear"]
    parts = [f"Commitments from your sent email and chat in the last {window_days} days:"]
    if open_items:
        parts.append(_table(open_items, today))
    else:
        parts.append("Nothing still open.")
    if unclear:
        lines = [f"{p.id}. {_cell(p.who)}: {_cell(_what(p))} ({_source(p)})" for p in unclear]
        parts.append("**Unclear** (a later message may have closed these):\n" + "\n".join(lines))
    parts.append(_footer(ledger, dropped, window_days))
    parts.extend(notes)  # sources that could not be searched, the step limit
    return "\n\n".join(part for part in parts if part)


def _table(items: Sequence[Promise], today: date) -> str:
    header = "| # | To | What | Due | Overdue | Source link |\n|---|---|---|---|---|---|"
    rows = [
        f"| {p.id} | {_cell(p.who)} | {_cell(_what(p))} | {p.due_date or NO_DATE} | {_overdue(p, today)} | "
        f"{_cell(_source(p))} |"
        for p in items
    ]
    return "\n".join([header, *rows])


def _footer(ledger: Ledger, dropped: int, window_days: int) -> str:
    notes = []
    if ledger.done:
        notes.append(f"{ledger.done} already delivered (not shown).")
    if ledger.capped:
        notes.append(f"Showing the first {len(ledger.items)}; {ledger.capped} more not shown.")
    if dropped:
        notes.append(f"Left out {dropped} candidate(s) I couldn't back with a message you sent in the last "
                     f"{window_days} days.")
    if ledger.items:
        notes.append("Say \"#1, draft it\" and I'll draft a reply for you to approve before anything is sent.")
    return " ".join(notes)


def _what(p: Promise) -> str:
    return p.topic or p.quote


def _overdue(p: Promise, today: date) -> str:
    days = overdue_days(p, today)
    if days is None:
        return "-"
    if days > 0:
        return f"{days} day{'s' if days != 1 else ''}"
    return "due today" if days == 0 else f"due in {-days} day{'s' if days != -1 else ''}"


def _source(p: Promise) -> str:
    return p.link if p.link.startswith(("http://", "https://")) else f"{p.source} message {p.link}"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ").strip()


def render_for_followup(items: Sequence[Promise], outcomes: Mapping[str, str], today: date) -> str:
    """The stored list as the follow-up and draft models see it (fenced by the caller)."""
    return "\n".join(_followup_line(p, outcomes.get(p.id), today) for p in items)


def _followup_line(p: Promise, outcome: str | None, today: date) -> str:
    status = f"; {_OUTCOME_LABELS.get(outcome, outcome)}" if outcome else ""
    return (f"#{p.id} [{p.state}{status}] to {p.who} via {p.source}, sent {p.sent_at}: \"{p.quote}\" "
            f"(topic: {p.topic}; due: {p.due_date or NO_DATE}; overdue: {_overdue(p, today)}; "
            f"thread: {p.thread_ref}; link: {p.link})")


def render_copyable(promise_id: str, who: str, subject: str, body: str, reason: str) -> str:
    """A draft the agent cannot send, offered for the user to copy."""
    subject_line = f"Subject: {subject}\n" if subject else ""
    return f"#{promise_id} to {who}: {reason} Here is the text to copy:\n\n{subject_line}{body}"
