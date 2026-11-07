"""User-facing text and the output envelope: the ranked table, the list given to the follow-up and draft
models, the chat digest, a message to copy, and the JSON envelope. Every number shown here is computed in
code (scoring.py); every URL comes from tool output."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

import scoring
from failures import Failure

_OUTCOME_LABELS = {"sent": "email sent", "skipped": "draft skipped", "denied": "send denied",
                   "blocked": "sending blocked", "not_sent": "email not sent"}
_NEEDS_HUMAN_CLASSES = frozenset({"fraud_block", "unknown"})


@dataclass(frozen=True)
class ListMeta:
    """What the header and footer say about the list, kept so a follow-up can re-render it."""

    window_days: int
    dropped: int = 0
    filtered: int = 0
    capped: int = 0
    notes: tuple[str, ...] = ()
    livemode: bool | None = None
    salvaged: bool = False
    pasted: bool = False
    history_ran: bool = False
    contact_ran: bool = False

    def to_dict(self) -> dict:
        return {**asdict(self), "notes": list(self.notes)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None, default_window: int) -> ListMeta:
        raw = dict(data or {})
        names = {f.name for f in fields(ListMeta)}
        values = {k: v for k, v in raw.items() if k in names}
        return ListMeta(**{**values, "window_days": int(raw.get("window_days") or default_window),
                           "notes": tuple(str(n) for n in raw.get("notes") or ())})


@dataclass(frozen=True)
class View:
    """The inputs every rendering needs."""

    now: datetime
    outcomes: Mapping[str, str]
    drafted: Mapping[str, str]  # failure key → the drafted body, this conversation

    def scored(self, f: Failure) -> scoring.Scored:
        return scoring.score(f, self.now)


# --- the table -------------------------------------------------------------------------------------------

def header(items: Sequence[Failure], meta: ListMeta, view: View) -> str:
    mode = " (test mode)" if meta.livemode is False else ""
    total = " + ".join(scoring.money(v, c) for c, v in scoring.totals(items).items())
    high = sum(1 for f in items if view.scored(f).high_value)
    span = "from the rows you pasted" if meta.pasted else f"last {meta.window_days} days"
    return f"Failed payments, {span}{mode}: {len(items)} failed, {total} at risk ({high} high value)"


def render_list(items: Sequence[Failure], meta: ListMeta, view: View) -> str:
    parts = [header(items, meta, view), _table(items, view), _footer(items, meta, view)]
    parts.extend(meta.notes)
    return "\n\n".join(part for part in parts if part)


def _table(items: Sequence[Failure], view: View) -> str:
    head = ("| # | Customer | Plan | Amount | Reason | Next retry | Priority | Note |\n"
            "|---|---|---|---|---|---|---|---|")
    return "\n".join([head, *(_row(view.scored(f), view) for f in items)])


def _row(s: scoring.Scored, view: View) -> str:
    f = s.failure
    reason = s.reason.klass + (f" ({f.decline_code})" if f.decline_code and f.decline_code != s.reason.klass else "")
    customer = f.who if not f.link else f"[{f.who}]({f.link})"
    cells = (f.number, customer, f.plan_name or "unknown", scoring.money(f.amount_minor, f.currency), reason,
             next_retry(s), s.priority, _note(s, view))
    return "| " + " | ".join(_cell(str(c)) for c in cells) + " |"


def next_retry(s: scoring.Scored) -> str:
    days = s.urgency.days_to_next_attempt
    if days is None:
        return "unknown" if s.failure.pasted else "none left"
    when = "today" if days == 0 else f"in {days}d" if days > 0 else f"{-days}d ago"
    return f"{scoring.parse_time(s.failure.next_attempt_at).date().isoformat()} ({when})"


def _note(s: scoring.Scored, view: View) -> str:
    f = s.failure
    notes = ["high value"] if s.high_value else []
    if f.attempt_count and f.attempt_count > 1:
        notes.append(f"attempt {f.attempt_count}")
    if f.contacted_at:
        notes.append(f"emailed {f.contacted_at}")
    if f.disputes:
        notes.append("dispute on file")
    outcome = view.outcomes.get(f.key)
    if outcome:
        notes.append(_OUTCOME_LABELS.get(outcome, outcome))
    return ", ".join(notes) or "-"


def _footer(items: Sequence[Failure], meta: ListMeta, view: View) -> str:
    notes = []
    if meta.dropped:
        notes.append(f"{meta.dropped} row(s) dropped: not found in tool output or outside the window.")
    if meta.filtered:
        notes.append(f"{meta.filtered} left out by your filters.")
    if meta.capped:
        notes.append(f"Showing {len(items)}; {meta.capped} more not shown.")
    if len(scoring.totals(items)) > 1:
        notes.append("Thresholds are applied in each invoice's own currency; nothing is converted.")
    if any(view.scored(f).high_value for f in items):
        notes.append("Chat digest ready for the high-value ones: say \"post to #channel\".")
    if items:
        notes.append("Say \"draft for #1 and #2\" and I'll draft recovery emails for you to approve before "
                     "anything is sent.")
    return " ".join(notes)


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ").strip()


def render_for_model(items: Sequence[Failure], view: View) -> str:
    """The stored list as the follow-up and draft models see it (fenced by the caller)."""
    return "\n".join(model_line(view.scored(f), view) for f in items)


def model_line(s: scoring.Scored, view: View) -> str:
    f = s.failure
    outcome = view.outcomes.get(f.key)
    status = f"; {_OUTCOME_LABELS.get(outcome, outcome)}" if outcome else ""
    return (f"#{f.number} [priority {s.priority}; {'high value' if s.high_value else 'not high value'}; "
            f"reason {s.reason.klass}; recoverable {s.reason.recoverable}; urgency {s.urgency.level}{status}] "
            f"customer {f.customer_name or 'name unknown'} <{f.customer_email}>, plan {f.plan_name or 'unknown'}, "
            f"amount {scoring.money(f.amount_minor, f.currency)}, decline code {f.decline_code or 'unknown'}, "
            f"attempt {f.attempt_count or 'unknown'}, next retry {next_retry(s)}, "
            f"successful payments {f.successful_payments if f.history_checked else 'unknown'}, "
            f"prior contact {f.contacted_at or ('none' if f.contact_checked else 'not checked')}, "
            f"invoice {f.invoice_id or f.id}, link {f.link or 'none'}")


# --- digest and copyable text --------------------------------------------------------------------------

def digest(items: Sequence[Failure], view: View) -> str:
    """The chat digest of high-value failures: names, amounts and totals from checked data only."""
    high = [f for f in items if view.scored(f).high_value]
    total = ", ".join(scoring.money(v, c) for c, v in scoring.totals(high).items())
    lines = [f"Failed payments needing attention: {len(high)} high value, {total} at risk"]
    for f in high:
        s = view.scored(f)
        plan = f" ({f.plan_name})" if f.plan_name else ""
        lines.append(f"- {f.who}{plan} {scoring.money(f.amount_minor, f.currency)}, {s.reason.klass}, "
                     f"next attempt {next_retry(s)}")
    return "\n".join(lines)


def render_copyable(number: str, who: str, subject: str, body: str, reason: str) -> str:
    """A message the agent cannot send, offered for the user to copy."""
    subject_line = f"Subject: {subject}\n" if subject else ""
    label = f"#{number} to {who}" if number else f"To {who}"
    return f"{label}: {reason} Here is the text to copy:\n\n{subject_line}{body}"


# --- the envelope -----------------------------------------------------------------------------------------

class FailureItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    number: str
    customer: str
    customer_email: str
    amount: str
    amount_minor: int
    currency: str
    draft: str | None
    high_value: bool
    klass: str = Field(serialization_alias="class")
    recoverable: str
    urgency: str
    priority: str
    decline_code: str | None
    next_attempt: str
    link: str | None


class Envelope(BaseModel):
    summary: str
    items: list[FailureItem]
    confidence: str
    needs_human: bool
    failures: list[FailureItem]
    chat_post: str | None


def confidence(meta: ListMeta) -> str:
    if meta.pasted or meta.salvaged:
        return "low"
    if not meta.history_ran or not meta.contact_ran or meta.dropped:
        return "medium"
    return "high"


def needs_human(items: Sequence[Failure], meta: ListMeta, view: View) -> bool:
    risky = any(view.scored(f).high_value and view.scored(f).reason.klass in _NEEDS_HUMAN_CLASSES for f in items)
    return risky or any(f.disputes for f in items) or bool(meta.capped) or meta.salvaged


def envelope(items: Sequence[Failure], meta: ListMeta, view: View) -> Envelope:
    rows = [_item(view.scored(f), view) for f in items]
    has_high = any(r.high_value for r in rows)
    return Envelope(summary=header(items, meta, view), items=rows, confidence=confidence(meta),
                    needs_human=needs_human(items, meta, view), failures=rows,
                    chat_post=digest(items, view) if has_high else None)


def _item(s: scoring.Scored, view: View) -> FailureItem:
    f = s.failure
    return FailureItem(number=f.number, customer=f.who, customer_email=f.customer_email,
                       amount=scoring.money(f.amount_minor, f.currency), amount_minor=f.amount_minor,
                       currency=f.currency, draft=view.drafted.get(f.key), high_value=s.high_value,
                       klass=s.reason.klass, recoverable=s.reason.recoverable, urgency=s.urgency.level,
                       priority=s.priority, decline_code=f.decline_code, next_attempt=next_retry(s), link=f.link)


def envelope_dict(env: Envelope) -> dict:
    return env.model_dump(by_alias=True)


def json_block(env: Envelope) -> str:
    return "```json\n" + json.dumps(envelope_dict(env), ensure_ascii=False, indent=1) + "\n```"
