"""User-facing text: the sweep table, the bug report (ship verdict + tickets grouped by customer), the
listing the follow-up model sees, and a reply offered to copy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import replies
import tiers
from config import Config
from records import Bug, ShipVerdict, Ticket
from steps_tickets import TicketOutcome

NO_QUOTE = "(complaint not shown: couldn't verify the quote)"
_OUTCOME_LABELS = {"sent": "reply sent", "solved": "reply sent, solved", "skipped": "skipped",
                   "denied": "send denied", "blocked": "posting blocked", "not_sent": "not sent"}


def sweep_table(choices: Sequence[Bug], no_fix: Sequence[Bug], days: int, notes: Sequence[str] = ()) -> str:
    rows = [f"| {n} | {_cell(b.key)} | {_cell(b.summary)} | {_cell(b.status)} | {_cell(', '.join(b.fix_versions))} "
            f"| {len(b.remote_links)} |" for n, b in enumerate(choices, start=1)]
    rows += [f"| - | {_cell(b.key)} | {_cell(b.summary)} | {_cell(b.status)} | no fix version: can't confirm it "
             f"shipped | {len(b.remote_links)} |" for b in no_fix]
    head = (f"Bugs that reached a done or released status in the last {days} days:\n\n"
            "| # | Key | Summary | Status | Fix version | Linked items |\n|---|---|---|---|---|---|")
    tail = ("Pick one and I'll check its release on the code host and find the customers waiting on it."
            if choices else "None has a fix version, so I can't confirm any of them shipped.")
    return "\n\n".join(["\n".join([head, *rows]), tail, *notes])


def verdict_line(verdict: ShipVerdict) -> str:
    if verdict.shipped:
        link = f" ({verdict.tag_url})" if verdict.tag_url else ""
        return f"Release check: **shipped** in {verdict.version}: {verdict.reason}{link}."
    return f"Release check: **not confirmed**. {replies.not_shipped(verdict.verdict, verdict.reason)}"


def report(bug: Bug, verdict: ShipVerdict, found: TicketOutcome | None, config: Config, today: date,
           notes: Sequence[str] = ()) -> str:
    title = f"**{bug.key}**: {_cell(bug.summary)}" + (f" ({bug.url})" if bug.url else "")
    parts = [title, verdict_line(verdict)]
    if found is not None:
        parts.append(_tickets(found, config, today))
    return "\n\n".join([*parts, *dict.fromkeys(n for n in notes if n)])


def _tickets(found: TicketOutcome, config: Config, today: date) -> str:
    if not found.tickets:
        return replies.NOTHING_FOUND if found.searched else ""
    head = (f"Open support tickets ({len(found.tickets)}), grouped by customer:\n\n"
            "| Ticket | Customer | Tier | Opened | Match | Original complaint |\n|---|---|---|---|---|---|")
    rows = [_row(t, today) for g in tiers.group(found.tickets, config) for t in g.tickets]
    extra = []
    if found.dropped:
        extra.append(f"Left out {found.dropped} candidate(s): not backed by the support desk's own data, already "
                     "solved, or a weak or old keyword match.")
    if found.capped:
        extra.append(f"{found.capped} more not kept (cap {config.max_tickets}).")
    return "\n\n".join(["\n".join([head, *rows]), *extra])


def _row(t: Ticket, today: date) -> str:
    age = tiers.age_days(t, today)
    opened = f"{age} days ago" if age is not None else "?"
    ticket = f"[#{t.id}]({t.url})" if t.url else f"#{t.id}"
    quote = f"\"{_cell(t.complaint_quote)}\"" if t.complaint_quote else NO_QUOTE
    match = "Linked" if t.match == "linked" else "Keyword match"
    return f"| {ticket} | {_cell(tiers.customer_of(t))} | {t.tier} | {opened} | {match} | {quote} |"


def for_followup(bug: Bug, verdict: ShipVerdict, tickets: Sequence[Ticket], outcomes: Mapping[str, str]) -> str:
    """The stored result as the follow-up model sees it (fenced by the caller)."""
    lines = [f"Bug {bug.key}: {bug.summary} (status {bug.status}; fix versions {', '.join(bug.fix_versions)})",
             f"Release verdict: {verdict.verdict} ({verdict.reason})"]
    for t in tickets:
        state = f"; {_OUTCOME_LABELS.get(outcomes[t.id], outcomes[t.id])}" if t.id in outcomes else ""
        lines.append(f"Ticket #{t.id} [{t.match}{state}] customer {tiers.customer_of(t)}, tier {t.tier}, opened "
                     f"{t.created_at or '?'}, subject: {t.subject or '?'}")
    return "\n".join(lines)


def copyable(ticket_id: str, customer: str, body: str, reason: str) -> str:
    """A reply the agent cannot post, offered for the user to copy."""
    return f"#{ticket_id} ({customer}): {reason} Here is the text to copy:\n\n{body}"


def _cell(text: str) -> str:
    return (text or "").replace("|", "/").replace("\n", " ").strip()
