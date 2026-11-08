"""Pure: per-team buckets for the rollup. Counts, dates and membership are code; no team or person is
ranked or scored. Teams are listed alphabetically; shipped items newest-completed first.

- shipped: status type completed and completed inside the window
- in flight: status type started
- blocked: "blocked" (whole word) in a label or the status name, or an open blocking issue
- next week: due today through today + 7 and not completed or cancelled
- carry-over: in flight now and created before the window started
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dates import Window
from records import Event, Issue

LAUNCH_WORDS = re.compile(r"\b(?:launch|launches|release|releases|freeze|review|demo|deadline|board)\b", re.IGNORECASE)
_BLOCKED_WORD = re.compile(r"\bblocked\b", re.IGNORECASE)


@dataclass(frozen=True)
class TeamRollup:
    team: str
    shipped: tuple[Issue, ...]
    in_flight: tuple[Issue, ...]
    blocked: tuple[Issue, ...]
    next_week: tuple[Issue, ...]
    carry_over: int
    capped: bool = False

    def headline(self) -> str:
        return (f"{len(self.shipped)} shipped, {len(self.in_flight)} in flight, {len(self.blocked)} blocked, "
                f"{len(self.next_week)} due next week")

    def counts(self) -> dict:
        return {"shipped": len(self.shipped), "in_flight": len(self.in_flight), "blocked": len(self.blocked),
                "next_week": len(self.next_week), "carry_over": self.carry_over}


def is_blocked(issue: Issue) -> bool:
    if issue.status_type in ("completed", "canceled"):
        return False
    named = any(_BLOCKED_WORD.search(text) for text in (*issue.labels, issue.status))
    return named or bool(issue.open_blockers)


def build(issues: Sequence[Issue], window: Window, teams: Sequence[str] = (),
          capped: Sequence[str] = ()) -> tuple[TeamRollup, ...]:
    names = {t.casefold(): t for t in (*teams, *(i.team for i in issues))}
    return tuple(_team(name, [i for i in issues if i.team.casefold() == key], window, name in capped)
                 for key, name in sorted(names.items()))


def _team(team: str, issues: list[Issue], window: Window, capped: bool) -> TeamRollup:
    shipped = sorted((i for i in issues if i.status_type == "completed" and window.in_window(i.completed_at)),
                     key=lambda i: (i.completed_at, i.key), reverse=True)
    in_flight = [i for i in issues if i.status_type == "started"]
    due = sorted((i for i in issues if window.in_next_week(i.due) and i.status_type not in ("completed", "canceled")),
                 key=lambda i: (i.due, i.key))
    carry = sum(1 for i in in_flight if i.created_at is not None and i.created_at < window.start)
    return TeamRollup(team=team, shipped=tuple(shipped), in_flight=tuple(sorted(in_flight, key=lambda i: i.key)),
                      blocked=tuple(sorted((i for i in issues if is_blocked(i)), key=lambda i: i.key)),
                      next_week=tuple(due), carry_over=carry, capped=capped)


def launch_events(events: Sequence[Event]) -> tuple[Event, ...]:
    """Calendar events next week whose title carries a launch word."""
    return tuple(e for e in events if LAUNCH_WORDS.search(e.title))
