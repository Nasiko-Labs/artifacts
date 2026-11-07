"""Pure: is an open or waiting item hot, and why? Each reason is the matched cue and a quote from the notes.

- a cue word in any entry about it (escalated, urgent, asap, p0/p1, sev1, critical, outage, down, breach, SLA
  breach/risk/expire/today, customer waiting, vip, exec, cancel, legal, chargeback, third time, still not,
  data loss);
- a due that parses to a moment within 12 hours of the handoff, or already past (unparseable dues are shown
  as written, never used here);
- it came up in 3 or more entries."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime

import duetime
import grounding
from entries import Entry
from items import RESOLVED, Item, Reason
from status import item_texts

RECURRING_MIN_ENTRIES = 3
QUOTE_CHARS = 100
QUOTE_CONTEXT = 40

CUES: tuple[tuple[str, re.Pattern], ...] = tuple((label, re.compile(pattern, re.IGNORECASE)) for label, pattern in (
    ("escalated", r"\bescalat\w*"),
    ("urgent", r"\burgent\b"),
    ("asap", r"\basap\b"),
    ("P0/P1", r"\bp[01]\b"),
    ("sev1", r"\bsev\s?1\b"),
    ("critical", r"\bcritical\b"),
    ("outage", r"\boutage\b"),
    ("down", r"\b(?:is|are|went|go(?:ne)?|still|completely)\s+down\b|\bdown for\b"),
    ("SLA", r"\bsla\b[^.;\n]{0,30}\b(?:breach\w*|risk|expir\w*|today)\b|\bbreach\w*"),
    ("customer waiting", r"\bcustomer (?:is )?waiting\b"),
    ("VIP", r"\bvip\b"),
    ("exec", r"\bexecs?\b"),
    ("cancel", r"\bcancel\w*"),
    ("legal", r"\blegal\b"),
    ("chargeback", r"\bchargebacks?\b"),
    ("came back again", r"\b(?:third time|3rd time|still not)\b"),
    ("data loss", r"\bdata loss\b"),
))


def _quote(text: str, match: re.Match) -> str:
    start = max(0, match.start() - QUOTE_CONTEXT)
    end = min(len(text), match.end() + QUOTE_CONTEXT)
    window = text[start:end]
    if start > 0 and " " in window:
        window = window[window.index(" ") + 1:]
    if end < len(text) and " " in window:
        window = window[: window.rindex(" ")]
    return grounding.clip(window, QUOTE_CHARS)


def cue_reasons(texts: list[str]) -> list[Reason]:
    reasons: list[Reason] = []
    for label, pattern in CUES:
        for text in texts:
            match = pattern.search(text)
            if match:
                reasons.append(Reason(label, _quote(text, match)))
                break
    return reasons


def classify(item: Item, by_id: dict[str, Entry], handoff: datetime) -> Item:
    due_at = duetime.parse_due(item.due, handoff) if item.due else None
    base = replace(item, due_at=due_at.isoformat(timespec="minutes") if due_at else None,
                   due_unparsed=bool(item.due) and due_at is None, hot=False, hot_reasons=())
    if base.status == RESOLVED:
        return base
    reasons = cue_reasons(item_texts(item, by_id))
    if due_at is not None and duetime.within_window(due_at, handoff):
        late = "overdue" if due_at < handoff else f"due by {duetime.hhmm(due_at)}"
        reasons.append(Reason(late, item.due or ""))
    if len(item.entries) >= RECURRING_MIN_ENTRIES:
        reasons.append(Reason(f"came up in {len(item.entries)} entries", item.snippet))
    return replace(base, hot=bool(reasons), hot_reasons=tuple(reasons))
