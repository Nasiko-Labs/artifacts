"""User-facing text and the structured envelope: the ranked table, the list given to the follow-up and
draft models, a reminder to copy, the internal escalation note. Every number is computed in code."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import date
from typing import Any

import ladder
from checks import parse_day
from invoices import Entry

_OUTCOME_LABELS = {"sent": "reminder sent today", "skipped": "draft skipped", "denied": "send denied",
                   "blocked": "sending blocked", "not_sent": "reminder not sent", "noted": "escalation note given"}


@dataclass(frozen=True)
class ListMeta:
    """What the header and footer say, kept so a follow-up can re-render without a new read."""

    livemode: bool | None = None
    source: str = "tools"            # "tools" | "pasted"
    dropped: int = 0
    capped: int = 0
    no_due_date: int = 0
    filtered: int = 0
    salvaged: bool = False
    reminders_ran: bool = False
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {**asdict(self), "notes": list(self.notes)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> ListMeta:
        raw = dict(data or {})
        known = {f.name for f in fields(ListMeta)}
        values = {k: v for k, v in raw.items() if k in known}
        return ListMeta(**{**values, "notes": tuple(str(n) for n in raw.get("notes") or ())})


@dataclass(frozen=True)
class View:
    today: date
    outcomes: Mapping[str, str]

    def state(self, entry: Entry) -> ladder.LadderState:
        return ladder.assess(entry, self.today)


def _mode(meta: ListMeta) -> str:
    if meta.source == "pasted":
        return " (from the rows you pasted)"
    return {True: " (live mode)", False: " (test mode)"}.get(meta.livemode, "")


def render_list(entries: Sequence[Entry], meta: ListMeta, view: View) -> str:
    sums = ladder.totals(entries)
    head = (f"**Overdue invoices{_mode(meta)}: {len(entries)} overdue, {ladder.totals_text(sums)} outstanding**")
    parts = [head, _table(entries, view)]
    parts.extend(_internal_notes(entries, view))
    parts.append(_footer(entries, meta))
    parts.extend(meta.notes)
    return "\n\n".join(p for p in parts if p)


def _table(entries: Sequence[Entry], view: View) -> str:
    header = ("| # | Invoice | Customer | Amount | Due | Days late | Reminders | Next step | Link |\n"
              "|---|---|---|---|---|---|---|---|---|")
    return "\n".join([header, *(_row(e, view) for e in entries)])


def _row(e: Entry, view: View) -> str:
    s = view.state(e)
    inv = e.invoice
    cells = (e.id, inv.label, inv.customer, ladder.money(inv.amount_due_minor, inv.currency), inv.due,
             str(s.days_overdue), _reminders(e, s, view), _next(e, s, view), inv.hosted_url or inv.link or "-")
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _reminders(e: Entry, s: ladder.LadderState, view: View) -> str:
    if not e.reminders_checked:
        return "not checked"
    if not e.reminders:
        text = "0"
    else:
        last = max(parse_day(r.sent_at) for r in e.reminders)
        ago = (view.today - last).days
        text = f"{s.reminders_sent} ({ago}d ago{', cooling down' if s.cooling_down else ''})"
    return text + (", customer replied" if s.customer_replied else "")


def _next(e: Entry, s: ladder.LadderState, view: View) -> str:
    outcome = view.outcomes.get(e.key)
    action = ladder.next_action(e, s)
    flags = " (large amount)" if s.amount_escalation else ""
    return f"{action}{flags}" + (f"; {_OUTCOME_LABELS.get(outcome, outcome)}" if outcome else "")


def _internal_notes(entries: Sequence[Entry], view: View) -> list[str]:
    big = [e for e in entries if view.state(e).level == 4 and not e.invoice.auto_charge]
    if not big:
        return []
    names = ", ".join(f"#{e.id} {e.invoice.label} ({e.invoice.customer})" for e in big)
    return [f"Needs a human: {names}. I won't email these customers; ask for the escalation note and call them."]


def _footer(entries: Sequence[Entry], meta: ListMeta) -> str:
    notes = []
    if meta.capped:
        notes.append(f"Showing the top {len(entries)}; {meta.capped} more not shown.")
    if meta.filtered:
        notes.append(f"{meta.filtered} overdue invoice(s) outside your filters not shown.")
    if meta.no_due_date:
        notes.append(f"{meta.no_due_date} auto-charge invoice(s) without a due date not listed.")
    if meta.dropped:
        notes.append(f"Left out {meta.dropped} row(s) I couldn't back with the tool output.")
    if meta.reminders_ran:
        notes.append("Reminders are matched by invoice number or invoice link only.")
    if entries:
        notes.append("Say \"draft the next reminder for #2\" and I'll draft it for you to approve; nothing is sent "
                     "without your OK.")
    return " ".join(notes)


def _cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ").strip()


def render_for_model(entries: Sequence[Entry], view: View) -> str:
    """The stored list as the follow-up and draft models see it (fenced by the caller)."""
    return "\n".join(_model_line(e, view) for e in entries)


def _model_line(e: Entry, view: View) -> str:
    s = view.state(e)
    inv = e.invoice
    outcome = view.outcomes.get(e.key)
    status = f"; {_OUTCOME_LABELS.get(outcome, outcome)}" if outcome else ""
    reply = f"; customer replied, subject \"{e.reply.subject}\"" if e.reply else ""
    return (f"#{e.id} invoice {inv.label} for {inv.customer} <{inv.customer_email or 'no email on file'}>: "
            f"amount due {ladder.money(inv.amount_due_minor, inv.currency)}, due {inv.due}, "
            f"{s.days_overdue} days overdue, reminders sent {s.reminders_sent if e.reminders_checked else 'not checked'}, "
            f"level {s.level} {s.name}, next step {ladder.next_action(e, s)}{reply}{status}; "
            f"payment link {inv.hosted_url or '[payment link]'}")


def render_copyable(entry_id: str, who: str, subject: str, body: str, reason: str) -> str:
    """A reminder the agent cannot send, offered for the user to copy."""
    subject_line = f"Subject: {subject}\n" if subject else ""
    return f"#{entry_id} to {who}: {reason} Here is the text to copy:\n\n{subject_line}{body}"


def internal_note(e: Entry, view: View) -> str:
    """Level 4: a note to the user, never an email to the customer."""
    s = view.state(e)
    inv = e.invoice
    history = ("reminder history not checked" if not e.reminders_checked
               else "no reminders found" if not e.reminders
               else "reminders sent " + ", ".join(f"{parse_day(r.sent_at)} \"{r.subject or 'no subject'}\""
                                                  for r in e.reminders))
    reply = f" The customer replied on {parse_day(e.reply.received_at)}." if e.reply else ""
    return (f"#{e.id} {inv.label} ({inv.customer}) is an internal escalation, not a customer email: "
            f"{ladder.money(inv.amount_due_minor, inv.currency)} due {inv.due}, {s.days_overdue} days overdue; "
            f"{history}.{reply} Recommended: call the customer's billing contact"
            f"{' (' + inv.customer_email + ')' if inv.customer_email else ''} and agree a payment date.")


# --- the envelope ---------------------------------------------------------------------------------

def envelope(entries: Sequence[Entry], meta: ListMeta, view: View, summary: str,
             drafts: Mapping[str, str] | None = None) -> dict:
    states = {e.key: view.state(e) for e in entries}
    overdue = [_item(e, states[e.key], (drafts or {}).get(e.key)) for e in entries]
    return {"summary": summary, "items": overdue, "confidence": confidence(meta, entries),
            "needs_human": any(ladder.needs_human(e, states[e.key]) for e in entries), "overdue": overdue,
            "livemode": meta.livemode}


def _item(e: Entry, s: ladder.LadderState, draft: str | None) -> dict:
    inv = e.invoice
    return {"invoice_id": inv.id, "invoice": inv.label, "customer": inv.customer, "email": inv.customer_email,
            "amount": ladder.major_units(inv.amount_due_minor, inv.currency), "currency": inv.currency,
            "due_date": inv.due, "days_overdue": s.days_overdue, "reminders_sent": s.reminders_sent,
            "reminders_checked": e.reminders_checked, "level": s.level, "level_name": s.name,
            "next_action": ladder.next_action(e, s), "draft": draft, "link": inv.hosted_url or inv.link,
            "needs_human": ladder.needs_human(e, s)}


def confidence(meta: ListMeta, entries: Sequence[Entry]) -> str:
    if meta.source == "pasted" or meta.salvaged:
        return "low"
    if not meta.reminders_ran or meta.dropped or any(not e.reminders_checked for e in entries):
        return "medium"
    return "high"


def summary_line(entries: Sequence[Entry]) -> str:
    return f"{len(entries)} overdue invoice(s), {ladder.totals_text(ladder.totals(entries))} outstanding."


def as_json(data: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n```"
