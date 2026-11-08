"""Pure: the post-checks on everything the model returned. An id, date, number, quote or link is kept only
when it appears in this turn's tool output (`seen_text`); otherwise the item is dropped (ids, completion
dates) or the field is blanked (links, titles, other dates, estimates)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from dates import day
from metrics import fmt_points
from records import NO_REASON, Cycle, Evidence, Issue, Team

_WHITESPACE = re.compile(r"\s+")
_ESCAPES = (("\\n", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\u2019", "’"), ("\\u2018", "‘"))


def normalize(text: str) -> str:
    for escaped, plain in _ESCAPES:
        text = text.replace(escaped, plain)
    return _WHITESPACE.sub(" ", text).strip().casefold()


def has_token(seen_text: str, value: str) -> bool:
    return bool(value) and re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", seen_text) is not None


def contains(seen_text: str, value: str) -> bool:
    wanted = normalize(value)
    return bool(wanted) and wanted in normalize(seen_text)


def _seen_day(seen_text: str, value: str) -> str:
    """The value unchanged when its calendar day appears in tool output, else ""."""
    d = day(value)
    return value if d and d in seen_text else ""


@dataclass(frozen=True)
class Checked:
    kept: tuple
    dropped: int = 0
    capped: int = 0


def check_teams(teams: Sequence[Team], seen_text: str) -> Checked:
    kept = tuple({t.id: t for t in teams if has_token(seen_text, t.id) and contains(seen_text, t.name)}.values())
    return Checked(kept, len(teams) - len(kept))


def check_cycles(cycles: Sequence[Cycle], seen_text: str) -> Checked:
    kept = []
    for c in cycles:
        if not has_token(seen_text, c.id) or not _seen_day(seen_text, c.starts_at) or not _seen_day(seen_text, c.ends_at):
            continue
        kept.append(replace(c, number=c.number if has_token(seen_text, c.number) else "",
                            name=c.name if contains(seen_text, c.name) else ""))
    return Checked(tuple(kept), len(cycles) - len(kept))


def check_issues(issues: Sequence[Issue], seen_text: str, cap: int) -> Checked:
    kept: dict[str, Issue] = {}
    dropped = 0
    for issue in issues:
        completed = _seen_day(seen_text, issue.completed_at)
        if not has_token(seen_text, issue.id) or (issue.completed_at and not completed):
            dropped += 1
            continue
        estimate = issue.estimate if issue.estimate is not None and has_token(seen_text, fmt_points(issue.estimate)) \
            else None
        kept.setdefault(issue.id, replace(
            issue, url=issue.url if issue.url and issue.url in seen_text else "",
            title=issue.title if contains(seen_text, issue.title) else "", estimate=estimate,
            created_at=_seen_day(seen_text, issue.created_at),
            added_to_cycle_at=_seen_day(seen_text, issue.added_to_cycle_at)))
    ordered = tuple(kept.values())
    return Checked(ordered[:cap], dropped, max(0, len(ordered) - cap))


def check_evidence(evidence: Sequence[Evidence], seen_text: str, issue_ids: Iterable[str]) -> Checked:
    allowed = set(issue_ids)
    kept = tuple(replace(e, url=e.url if e.url and e.url in seen_text else "",
                         posted_at=_seen_day(seen_text, e.posted_at))
                 for e in evidence if e.issue_id in allowed and contains(seen_text, e.text))
    return Checked(kept, len(evidence) - len(kept))


def check_reason(issue_id: str, reason: str, quote: str,
                 evidence: Mapping[str, Sequence[Evidence]]) -> tuple[str, Evidence | None]:
    """The reason and the evidence item that holds the quote, or (no_reason_found, None)."""
    if reason == NO_REASON or not normalize(quote):
        return NO_REASON, None
    holder = next((e for e in evidence.get(issue_id, ()) if normalize(quote) in normalize(e.text)), None)
    return (reason, holder) if holder is not None else (NO_REASON, None)
