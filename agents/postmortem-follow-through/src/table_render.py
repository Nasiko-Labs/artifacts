"""Pure: everything the user reads, built in code from checked records: the table, the lists under
it, the listing the follow-up step answers from, nudge item lines, the reopen comment and the
"does this outage match" sentences."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from assess import MAX_ROWS_SHOWN, Assessment, Row
from dates import EVIDENCE_LOOKBACK_DAYS, Window
from records import Postmortem

ACT_HINT = "Say 'nudge #n' to message an owner, or 'reopen #n' for an item closed without evidence."
_CELL_MAX = 60


def _cell(text: str | None, limit: int = _CELL_MAX) -> str:
    clean = " ".join((text or "").split()).replace("|", "/")
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def render_report(assessment: Assessment, window: Window, notes: Sequence[str],
                  outcomes: Mapping[str, str] | None = None) -> str:
    head = (f"Postmortem action items from {window.start.isoformat()} to {window.today.isoformat()} "
            f"({window.months} months) that slipped:")
    parts = [head, render_table(assessment.rows, outcomes or {}) if assessment.rows else
             "No action item has slipped: every tracked item is assigned, touched in the last 30 days, "
             "or closed with evidence."]
    if assessment.wont_do:
        parts.append("Closed as won't-do (listed, never reopened):\n" + _bullets(assessment.wont_do))
    if assessment.untracked:
        parts.append("Not tracked (doc only):\n" + _bullets(assessment.untracked))
    if notes:
        parts.append("Coverage:\n" + _bullets(notes))
    if assessment.rows:
        parts.append(ACT_HINT)
    return "\n\n".join(parts)


def render_table(rows: Sequence[Row], outcomes: Mapping[str, str]) -> str:
    shown = rows[:MAX_ROWS_SHOWN]
    lines = ["| # | Action item (ticket) | Owner | Age | Idle | State | From postmortem (incident, date) | Repeat? |",
             "|---|---|---|---|---|---|---|---|"]
    for r in shown:
        state = r.state + _outcome_suffix(r, outcomes)
        lines.append(f"| {r.n} | {_cell(r.text)} ({r.key}) | {_cell(r.owner, 30)} | {r.age_days}d | {r.idle_days}d "
                     f"| {state} | {_cell(r.pm_label, 70)} | {r.repeat or '—'} |")
    if len(rows) > len(shown):
        lines.append(f"\n…and {len(rows) - len(shown)} more not shown.")
    return "\n".join(lines)


def _outcome_suffix(row: Row, outcomes: Mapping[str, str]) -> str:
    done = [v for k, v in ((f"nudge:{row.key}", "nudged"), (f"reopen:{row.key}", "reopened"))
            if outcomes.get(k) == v]
    return f" ({', '.join(done)})" if done else ""


def _bullets(lines: Sequence[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def render_for_followup(rows: Sequence[Row], outcomes: Mapping[str, str], wont_do: Sequence[str] = (),
                        untracked: Sequence[str] = ()) -> str:
    """The stored table as data for the follow-up step (it answers only from this)."""
    lines = [f"#{r.n} {r.key} \"{r.text}\" owner={r.owner} age={r.age_days}d idle={r.idle_days}d "
             f"state={r.state} status={r.status} from={r.pm_label} repeat={r.repeat or 'none'}"
             f"{_outcome_suffix(r, outcomes)}" for r in rows]
    lines += [f"won't-do: {w}" for w in wont_do] + [f"not tracked: {u}" for u in untracked]
    return "\n".join(lines) or "(no slipped items)"


def nudge_item_line(row: Row) -> str:
    link = f" {row.url}" if row.url else ""
    repeat = f" A repeat incident already happened: {row.repeat}." if row.repeat else ""
    return (f"- {row.key} {row.text}{link}: {row.state}, open {row.age_days} days, last touched "
            f"{row.idle_days} days ago. From {row.pm_label}.{repeat}")


def nudge_message(opening: str, rows: Sequence[Row]) -> str:
    return opening.strip() + "\n\n" + "\n".join(nudge_item_line(r) for r in rows)


def default_opening(owner: str) -> str:
    first = owner.split()[0] if owner.split() else "there"
    return f"Hi {first}, a quick nudge on postmortem follow-ups assigned to you:"


def reopen_comment(row: Row) -> str:
    incident = row.pm_incident or row.pm_label
    return (f"Reopened via Postmortem Follow-Through: closed on {row.resolved_on or 'an unknown date'} with no "
            f"linked change or comment in the {EVIDENCE_LOOKBACK_DAYS} days before closing. Originating "
            f"incident {incident}. Please add the PR or close with a reason.")


def match_sentences(incident: str, matches: Sequence[Postmortem], rows: Sequence[Row],
                    open_items: Mapping[str, Sequence[tuple[str, int]]]) -> str:
    """`open_items`: postmortem id → (ticket key, idle days) of its items that are not done."""
    if not matches:
        return f"{incident} doesn't match any postmortem from the window."
    lines = []
    for pm in matches:
        label = f"postmortem \"{pm.title}\" ({pm.key_or_url})"
        fixes = open_items.get(pm.id) or ()
        unverified = [r for r in rows if r.pm_id == pm.id and r.key not in {k for k, _ in fixes}
                      and r.flags and r.flags[0] in ("no_evidence", "not_verified")]
        if not fixes and not unverified:
            lines.append(f"This outage matches {label}; all of its tracked action items are done.")
        for key, idle in fixes:
            lines.append(f"This outage matches {label}, whose fix {key} is still open (untouched {idle} days).")
        for r in unverified:
            lines.append(f"This outage matches {label}, whose fix {r.key} was {r.state} on {r.resolved_on}.")
    return "\n".join(lines)


def render_copyable(header: str, text: str, reason: str) -> str:
    return f"{reason}\n\n{header}\n\nText to copy:\n{text}"
