"""Pure: the two status vocabularies, mapped in code. Copied from the Status Report Reality Check method
(2026-11-05); keep in sync by copying. Live-data additions: the tracker's `status_type` is the fallback
when no status word matches, an open `blocked_by` issue makes a ticket blocked, and "landed" is done.

- Ticket statuses (an export cell) map to done / in_progress / blocked / todo / cancelled, else unknown.
  A `Status map:` line in the user's message extends or overrides the map; a `blocked` / `impediment`
  label makes a ticket blocked whatever its status says.
- Reported status words (copied verbatim from the report) map to done / on_track / at_risk / blocked /
  not_started, else unknown. Longer phrases are matched first and consumed ("as planned" is on track, not
  "planned"); when several categories remain, the most cautious wins.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

DONE, IN_PROGRESS, BLOCKED, TODO, CANCELLED, UNKNOWN = "done", "in_progress", "blocked", "todo", "cancelled", "unknown"
ON_TRACK, AT_RISK, NOT_STARTED = "on_track", "at_risk", "not_started"

TICKET_MAP: dict[str, tuple[str, ...]] = {
    DONE: ("done", "closed", "resolved", "released", "shipped", "complete", "completed", "merged", "deployed"),
    IN_PROGRESS: ("in progress", "doing", "in development", "in review", "review", "in qa", "testing", "started",
                  "active", "code review"),
    BLOCKED: ("blocked", "on hold", "impediment", "waiting", "waiting on", "paused"),
    TODO: ("to do", "todo", "backlog", "open", "new", "ready", "selected", "planned", "not started",
           "selected for development", "ready for development"),
    CANCELLED: ("cancelled", "canceled", "won't do", "wont do", "duplicate", "rejected", "obsolete"),
}
BLOCKED_LABELS = frozenset({"blocked", "impediment", "flagged"})
CATEGORY_NAMES = {"done": DONE, "in progress": IN_PROGRESS, "in_progress": IN_PROGRESS, "blocked": BLOCKED,
                  "todo": TODO, "to do": TODO, "cancelled": CANCELLED, "canceled": CANCELLED}

REPORTED_MAP: dict[str, tuple[str, ...]] = {
    DONE: ("done", "complete", "completed", "shipped", "launched", "delivered", "released", "finished", "live",
           "landed"),
    ON_TRACK: ("on track", "green", "going well", "progressing", "in progress", "as planned", "no issues",
               "not blocked", "unblocked", "no blockers", "no concerns", "no risks", "on schedule"),
    AT_RISK: ("at risk", "amber", "yellow", "behind", "slipping", "delayed", "concern", "concerns", "risky", "tight",
              "behind schedule"),
    BLOCKED: ("blocked", "red", "stuck", "waiting on", "dependency"),
    NOT_STARTED: ("not started", "planned", "upcoming", "to start", "backlog", "not yet started"),
}
PRECEDENCE = (BLOCKED, AT_RISK, NOT_STARTED, ON_TRACK, DONE)

_SEPARATORS = re.compile(r"[_\-]+")
_SPACES = re.compile(r"\s+")
_REPORTED_PHRASES = sorted(((p, c) for c, ps in REPORTED_MAP.items() for p in ps), key=lambda pc: -len(pc[0]))
_TICKET_PHRASES = sorted(((p, c) for c, ps in TICKET_MAP.items() for p in ps), key=lambda pc: -len(pc[0]))
_TICKET_LOOKUP = {p: c for p, c in _TICKET_PHRASES}


def normalize(text: str) -> str:
    plain = _SEPARATORS.sub(" ", (text or "").casefold().replace("’", "'"))
    return _SPACES.sub(" ", plain).strip()


STATUS_TYPE_MAP = {"completed": DONE, "started": IN_PROGRESS, "unstarted": TODO, "backlog": TODO,
                   "canceled": CANCELLED, "cancelled": CANCELLED}


def ticket_category(status: str, labels: Iterable[str] = (), status_map: Mapping[str, str] | None = None,
                    status_type: str | None = None, open_blocker: bool = False) -> str:
    """The status name first; with no word match, the tracker's `status_type`; a blocked label or an open
    blocking issue makes the ticket blocked whatever its status says."""
    if open_blocker or any(normalize(label) in BLOCKED_LABELS for label in labels):
        return BLOCKED
    by_name = _by_name(status, status_map)
    if by_name != UNKNOWN:
        return by_name
    return STATUS_TYPE_MAP.get(normalize(status_type or ""), UNKNOWN)


def _by_name(status: str, status_map: Mapping[str, str] | None) -> str:
    key = normalize(status)
    if not key:
        return UNKNOWN
    overrides = {normalize(k): v for k, v in (status_map or {}).items()}
    if key in overrides:
        return overrides[key]
    if key in _TICKET_LOOKUP:
        return _TICKET_LOOKUP[key]
    for phrase, category in _TICKET_PHRASES:  # "waiting on customer", "in progress (dev)"
        if key.startswith((phrase + " ", phrase + " (")):
            return category
    return UNKNOWN


def reported_category(words: str) -> str:
    text = f" {normalize(words)} "
    found: set[str] = set()
    for phrase, category in _REPORTED_PHRASES:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])")
        if pattern.search(text):
            found.add(category)
            text = pattern.sub(" ", text)
    for category in PRECEDENCE:
        if category in found:
            return category
    return UNKNOWN


def parse_status_map(line: str) -> dict[str, str]:
    """`in review = in progress; qa = in progress` -> {status: category}; unreadable pairs are skipped."""
    mapping: dict[str, str] = {}
    for pair in re.split(r"[;,]", line or ""):
        left, sep, right = pair.partition("=")
        if not sep:
            left, sep, right = pair.partition("->")
        status, target = normalize(left), normalize(right)
        category = CATEGORY_NAMES.get(target) or _TICKET_LOOKUP.get(target)
        if sep and status and category:
            mapping[status] = category
    return mapping


def label(category: str) -> str:
    return category.replace("_", " ")
