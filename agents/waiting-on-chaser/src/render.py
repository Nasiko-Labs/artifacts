"""User-facing text: the ranked table, the list given to the follow-up and draft models, a nudge to copy.
Every number shown here is computed in code (scoring.py)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any

import scoring
from asks import Ask
from checks import MIN_AGE_DAYS, parse_day

_OUTCOME_LABELS = {"sent": "nudged today", "skipped": "nudge skipped", "denied": "nudge denied",
                   "blocked": "sending blocked", "not_sent": "nudge not sent"}
_MARKED_RELATIONSHIPS = ("manager", "external")


@dataclass(frozen=True)
class ListMeta:
    """What the footer says about the list, kept so a re-score can re-render it without a sweep."""

    window_days: int
    dropped: int = 0
    too_fresh: int = 0
    capped: int = 0
    answered: int = 0
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {**asdict(self), "notes": list(self.notes)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None, default_window: int) -> ListMeta:
        raw = dict(data or {})
        counts = {f.name: int(raw.get(f.name) or 0) for f in fields(ListMeta) if f.name not in ("window_days", "notes")}
        return ListMeta(window_days=int(raw.get("window_days") or default_window), **counts,
                        notes=tuple(str(n) for n in raw.get("notes") or ()))


@dataclass(frozen=True)
class View:
    """The inputs every rendering needs."""

    now: datetime
    me: tuple[str, ...]
    manager: str | None
    outcomes: Mapping[str, str]

    def scored(self, ask: Ask) -> scoring.Scored:
        return scoring.score(ask, self.now, self.me, self.manager)


def render_list(items: Sequence[Ask], meta: ListMeta, view: View) -> str:
    waiting = [a for a in items if a.reply_state == "waiting"]
    unclear = [a for a in items if a.reply_state == "unclear"]
    parts = [f"Who you're waiting on, from your sent email and chat in the last {meta.window_days} days:"]
    parts.append(_table(waiting, view) if waiting else "Nobody is still waiting to answer you.")
    if unclear:
        lines = [f"{a.id}. {_cell(a.who)}: {_cell(_what(a))} ({_cell(_source(a))})" for a in unclear]
        parts.append("**Couldn't confirm no reply** (they may already have answered; I won't offer these for a "
                     "nudge unless you name one):\n" + "\n".join(lines))
    parts.append(_footer(items, meta))
    parts.extend(meta.notes)
    return "\n\n".join(part for part in parts if part)


def _table(items: Sequence[Ask], view: View) -> str:
    header = ("| # | Priority | Person | What you asked | Waiting | Deadline | Blocked | Last nudge | Link |\n"
              "|---|---|---|---|---|---|---|---|---|")
    rows = [_row(view.scored(a), view) for a in items]
    return "\n".join([header, *rows])


def _row(s: scoring.Scored, view: View) -> str:
    a = s.ask
    person = a.who + (f" ({s.relationship})" if s.relationship in _MARKED_RELATIONSHIPS else "")
    waiting = f"{int(s.days_waiting)}d" + ("" if a.reply_checked else ", reply not checked")
    blocked = f'"{a.blocking_quote}"' if a.blocking_quote else "-"
    cells = (a.id, s.label, person, _what(a), waiting, _deadline(s), blocked, _last_nudge(s, view), _source(a))
    return "| " + " | ".join(_cell(str(c)) for c in cells) + " |"


def _what(a: Ask) -> str:
    return f'{a.gist}: "{a.quote}"' if a.gist else f'"{a.quote}"'


def _deadline(s: scoring.Scored) -> str:
    if s.days_to_deadline is None:
        return "-"
    day = s.ask.deadline_date
    return f"{day} (passed)" if s.days_to_deadline < 0 else f"{day} (today)" if s.days_to_deadline == 0 else day


def _last_nudge(s: scoring.Scored, view: View) -> str:
    outcome = view.outcomes.get(s.ask.key)
    if outcome:
        return _OUTCOME_LABELS.get(outcome, outcome)
    days = [d for d in (parse_day(n.at) for n in s.ask.later_nudges) if d is not None]
    if not days:
        return "-"
    return max(days).isoformat() + (" (recent)" if s.tier == "recent" else "")


def _footer(items: Sequence[Ask], meta: ListMeta) -> str:
    notes = []
    if meta.answered:
        notes.append(f"{meta.answered} already answered (not shown).")
    if meta.too_fresh:
        notes.append(f"{meta.too_fresh} ask(s) younger than {MIN_AGE_DAYS} days not listed (say \"include fresh\" "
                     "to see them).")
    if meta.capped:
        notes.append(f"Showing the top {len(items)}; {meta.capped} more not shown.")
    if meta.dropped:
        notes.append(f"Left out {meta.dropped} candidate(s) I couldn't back with a message you sent in the last "
                     f"{meta.window_days} days.")
    if items:
        notes.append("Say \"nudge #1\" and I'll draft a nudge for you to approve before anything is sent.")
    return " ".join(notes)


def _source(a: Ask) -> str:
    return a.link if a.link.startswith(("http://", "https://")) else f"{a.source} message {a.link}"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ").strip()


def render_for_model(items: Sequence[Ask], view: View) -> str:
    """The stored list as the follow-up and draft models see it (fenced by the caller)."""
    return "\n".join(_model_line(view.scored(a), view) for a in items)


def _model_line(s: scoring.Scored, view: View) -> str:
    a = s.ask
    outcome = view.outcomes.get(a.key)
    status = f"; {_OUTCOME_LABELS.get(outcome, outcome)}" if outcome else ""
    checked = "" if a.reply_checked else "; reply not checked"
    return (f"#{a.id} [{a.reply_state}{checked}{status}; priority {s.label}; nudge tier {s.tier}; "
            f"relationship {s.relationship}] to {a.who} <{a.to}> via {a.source}, asked {a.asked_at}: \"{a.quote}\" "
            f"(gist: {a.gist}; type: {a.ask_type}; waiting: {int(s.days_waiting)} days; "
            f"deadline: {a.deadline_date or 'none'}; blocked: {a.blocking_quote or 'none'}; "
            f"last nudge: {_last_nudge(s, view)}; thread: {a.thread_ref}; link: {a.link})")


def render_copyable(ask_id: str, who: str, subject: str, body: str, reason: str) -> str:
    """A nudge the agent cannot send, offered for the user to copy."""
    subject_line = f"Subject: {subject}\n" if subject else ""
    return f"#{ask_id} to {who}: {reason} Here is the text to copy:\n\n{subject_line}{body}"
