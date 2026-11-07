"""Pure: which action items slipped, decided in code from checked records. The model never judges
staleness or evidence; it only extracted the fields used here.

Per item with ticket t:
  unassigned               not done and no assignee
  untouched 30+            not done and idle >= 30 days (idle = today - max(updated, latest comment))
  closed without evidence  done, not won't-do, and no change link / dev link, no >= 40-char comment in
                           the 14 days before resolution, no attachment
  closed (not verified)    as above, but comments could not be read, so it can't be told
Priority = 3 repeat + 2 closed without evidence + 1 unassigned + 1 untouched; ties to the oldest.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from corpus import Comment
from dates import EVIDENCE_LOOKBACK_DAYS, STALE_DAYS, parse_date
from records import ActionItem, Postmortem, TicketStatus
from repeats import Repeat

MAX_ROWS_SHOWN = 25
EVIDENCE_COMMENT_CHARS = 40

WONT_DO = re.compile(r"won'?t (?:do|fix)|duplicate|cannot reproduce|can't reproduce|obsolete|declined|not a bug",
                     re.IGNORECASE)
_DONE = re.compile(r"\b(?:done|closed|resolved|complete|completed|fixed)\b", re.IGNORECASE)
_TODO = re.compile(r"\b(?:to ?do|open|reopened|backlog|new)\b", re.IGNORECASE)
_CHANGE_LINK = re.compile(r"/pull/\d+|/commit/[0-9a-f]{7,40}|/merge_requests/\d+", re.IGNORECASE)


def status_category(status: TicketStatus) -> str:
    """From the verbatim status name; the tool's own category only when the name says nothing."""
    name = status.status or ""
    if WONT_DO.search(name) or _DONE.search(name):
        return "done"
    if _TODO.search(name):
        return "todo"
    return status.status_category or "in_progress"


def is_wont_do(status: TicketStatus) -> bool:
    return bool(WONT_DO.search(status.resolution or "") or WONT_DO.search(status.status or ""))


@dataclass(frozen=True)
class Evidence:
    comments: tuple[Comment, ...] = ()
    change_link: bool = False  # a code-host change link in a call made for this ticket alone


def has_evidence(status: TicketStatus, evidence: Evidence) -> bool:
    if (status.dev_links or 0) >= 1 or (status.attachments or 0) >= 1 or evidence.change_link:
        return True
    if any(_CHANGE_LINK.search(link) for link in status.links):
        return True
    if any(c.has_change_link for c in evidence.comments):
        return True
    resolved = parse_date(status.resolved_at) or parse_date(status.updated_at)
    since = resolved - timedelta(days=EVIDENCE_LOOKBACK_DAYS)
    return any(c.chars >= EVIDENCE_COMMENT_CHARS and since <= (parse_date(c.created_at) or date.min) <= resolved
               for c in evidence.comments)


@dataclass(frozen=True)
class Row:
    n: str
    key: str
    text: str
    url: str | None
    owner: str
    owner_email: str | None
    age_days: int
    idle_days: int
    flags: tuple[str, ...]
    state: str
    pm_id: str
    pm_label: str
    pm_incident: str | None
    repeat: str | None
    priority: int
    status: str
    resolved_on: str | None


@dataclass(frozen=True)
class Assessment:
    rows: tuple[Row, ...]
    wont_do: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    unknown_status: int = 0
    not_verified: int = 0


def pm_label(pm: Postmortem) -> str:
    detail = ", ".join(p for p in (pm.incident_ref, pm.date_shown.isoformat()) if p)
    return f"PM: {pm.title} ({detail})"


def assess(today: date, postmortems: Sequence[Postmortem], items: Sequence[ActionItem],
           statuses: Mapping[str, TicketStatus], evidence: Mapping[str, Evidence],
           repeats: Mapping[str, Repeat], comments_unavailable: bool) -> Assessment:
    pms = {pm.id: pm for pm in postmortems}
    rows: list[Row] = []
    wont_do: list[str] = []
    untracked: list[str] = []
    unknown = not_verified = 0
    for item in items:
        pm = pms.get(item.postmortem_id)
        if pm is None:
            continue
        if not item.ticket_key:
            age = (today - pm.created).days
            untracked.append(f"{item.text} ({pm_label(pm)}; {age}d, not tracked)")
            continue
        status = statuses.get(item.ticket_key)
        if status is None:
            unknown += 1
            continue
        row = _row(today, item, pm, status, evidence.get(item.ticket_key, Evidence()), repeats.get(pm.id),
                   comments_unavailable)
        if row == "wont_do":
            wont_do.append(f"{item.ticket_key} {item.text} ({status.resolution or status.status}; {pm_label(pm)})")
        elif row is not None:
            not_verified += "not_verified" in row.flags
            rows.append(row)
    ordered = sorted(rows, key=lambda r: (-r.priority, -r.age_days, r.key))
    numbered = tuple(_numbered(r, n) for n, r in enumerate(ordered, 1))
    return Assessment(numbered, tuple(wont_do), tuple(untracked), unknown, not_verified)


def _numbered(row: Row, n: int) -> Row:
    return Row(**{**row.__dict__, "n": str(n)})


def _row(today: date, item: ActionItem, pm: Postmortem, status: TicketStatus, evidence: Evidence,
         repeat: Repeat | None, comments_unavailable: bool) -> Row | str | None:
    category = status_category(status)
    created = parse_date(status.created_at)
    last_touch = max([parse_date(status.updated_at), *(parse_date(c.created_at) for c in evidence.comments)],
                     key=lambda d: d or date.min)
    age, idle = (today - created).days, (today - last_touch).days
    owner = status.assignee_name or status.assignee_email
    flags: tuple[str, ...] = ()
    if category == "done":
        if is_wont_do(status):
            return "wont_do"
        if has_evidence(status, evidence):
            return None
        flags = ("not_verified",) if comments_unavailable else ("no_evidence",)
    else:
        flags = (*(("unassigned",) if not owner else ()), *(("untouched",) if idle >= STALE_DAYS else ()))
    if not flags and repeat is None:
        return None
    repeat_text = f"{repeat.incident_id} {repeat.on}" if repeat else None
    priority = 3 * bool(repeat) + 2 * ("no_evidence" in flags) + ("unassigned" in flags) + ("untouched" in flags)
    resolved = parse_date(status.resolved_at) or (parse_date(status.updated_at) if category == "done" else None)
    return Row(n="", key=item.ticket_key, text=item.text, url=status.url, owner=owner or "unassigned",
               owner_email=status.assignee_email, age_days=age, idle_days=idle, flags=flags,
               state=state_label(flags), pm_id=pm.id, pm_label=pm_label(pm), pm_incident=pm.incident_ref,
               repeat=repeat_text, priority=priority, status=status.status,
               resolved_on=resolved.isoformat() if resolved else None)


def state_label(flags: Sequence[str]) -> str:
    if "no_evidence" in flags:
        return "closed without evidence"
    if "not_verified" in flags:
        return "closed (not verified)"
    words = [w for w in ("unassigned", "untouched") if w in flags]
    return " · ".join(words) or "open"


def open_by_postmortem(today: date, items: Sequence[ActionItem], statuses: Mapping[str, TicketStatus],
                       evidence: Mapping[str, Evidence]) -> dict[str, list[list]]:
    """Postmortem id → [ticket key, idle days] of its items that are not done (for "does X match")."""
    found: dict[str, list[list]] = {}
    for item in items:
        status = statuses.get(item.ticket_key or "")
        if status is None or status_category(status) == "done":
            continue
        comments = evidence.get(status.key, Evidence()).comments
        last = max([parse_date(status.updated_at), *(parse_date(c.created_at) for c in comments)],
                   key=lambda d: d or date.min)
        found.setdefault(item.postmortem_id, []).append([status.key, (today - last).days])
    return found
