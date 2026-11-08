"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import reality
import statuses as st
from coverage import Coverage
from dates import Window, plural
from linking import EXPLICIT, TITLE
from records import Event, Issue
from rollup import TeamRollup

HIGH_MATCHED = 0.8
HIGH_DATES = 0.9
LOW_MATCHED = 0.5
LOW_UNKNOWN = 0.25
MIN_POSTS = 3
_INTERNAL = ("order", "headline", "item_id", "days_since_update")
_LEVELS = ("low", "medium", "high")


@dataclass(frozen=True)
class Run:
    """Everything one pull produced, checked."""

    window: Window
    coverage: Coverage
    issues: tuple[Issue, ...]
    rollups: tuple[TeamRollup, ...]
    events: tuple[Event, ...]
    items: tuple[dict, ...] = ()           # report items, matched
    check: dict | None = None              # reality.run result; None when the check did not run
    conflicts: tuple[dict, ...] = ()
    previous: tuple[dict, ...] = ()
    capped: tuple[str, ...] = ()
    partial: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    fallback: bool = False
    posts: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)
    ctx: reality.Ctx | None = None


def reality_confidence(run: Run) -> str | None:
    if run.check is None:
        return None
    items, issues, ctx_unknown = run.items, run.issues, _unknown_share(run)
    matched = sum(1 for i in items if i["match"]["kind"] in (EXPLICIT, TITLE)) / len(items) if items else 0.0
    if matched < LOW_MATCHED or ctx_unknown > LOW_UNKNOWN or run.fallback:
        return "low"
    readable = sum(1 for i in issues if not i.updated_unparsed) / len(issues) if issues else 0.0
    return "high" if matched >= HIGH_MATCHED and readable >= HIGH_DATES else "medium"


def coverage_confidence(run: Run) -> str:
    missing = len(run.coverage.failed)
    if missing >= 2 or run.posts < MIN_POSTS:
        return "low"
    if missing == 1 or run.capped:
        return "medium"
    return "high"


def confidence(run: Run) -> str:
    levels = [c for c in (reality_confidence(run), coverage_confidence(run)) if c]
    return min(levels, key=_LEVELS.index)


def previous_status(previous: Sequence[dict], found: Sequence[dict]) -> dict:
    """A previous rollup's disagreements: still open when the same item (or one of its tickets) disagrees now."""
    now_items = {d["item"].casefold() for d in found}
    now_tickets = {t for d in found for t in d["tickets"]}
    still, resolved = [], []
    for d in previous:
        tickets = {t for t in d.get("tickets") or [] if isinstance(t, str)}
        same = d["item"].casefold() in now_items or bool(tickets & now_tickets)
        (still if same else resolved).append(d["item"])
    return {"still_open": still, "resolved": resolved}


def summary(run: Run) -> str:
    totals = {k: sum(r.counts()[k] for r in run.rollups) for k in ("shipped", "in_flight", "blocked", "next_week")}
    text = (f"Rollup of {plural(len(run.rollups), 'team')} for {run.window.start.isoformat()} to "
            f"{run.window.today.isoformat()}: {totals['shipped']} shipped, {totals['in_flight']} in flight, "
            f"{totals['blocked']} blocked, {totals['next_week']} due next week.")
    if run.check is None:
        return text + " Reality check not run."
    found, unmatched = run.check["disagreements"], run.check["unmatched"]
    high = sum(1 for d in found if d["risk"] == reality.HIGH)
    return (text + f" Compared {plural(run.check['report_items'], 'report item')} with "
            f"{plural(run.check['tickets'], 'ticket')}: {plural(len(found), 'disagreement')} ({high} high), "
            f"{len(unmatched)} unmatched.")


def build(run: Run, caveats: Sequence[str]) -> dict:
    found = [{k: v for k, v in d.items() if k not in _INTERNAL} for d in (run.check or {}).get("disagreements", [])]
    level = confidence(run)
    env = {
        "summary": summary(run), "items": found, "disagreements": found,
        "unmatched": (run.check or {}).get("unmatched", []), "caveats": list(caveats), "confidence": level,
        "needs_human": level == "low" or any(d["risk"] == reality.HIGH for d in found),
        "teams": [_team(r) for r in run.rollups], "conflicting_reports": list(run.conflicts),
        "coverage": [{"source": s.source, "state": s.state, "reason": s.reason} for s in run.coverage.statuses],
        "window": {"start": run.window.start.isoformat(), "end": run.window.today.isoformat(), "days": run.window.days},
        "next_week_events": [{"id": e.id, "title": e.title, "url": e.url, "start": e.start.isoformat()}
                             for e in run.events],
        "reality_check_ran": run.check is not None,
    }
    if run.check is not None and "unreported_risks" in run.check:
        env["unreported_risks"] = run.check["unreported_risks"]
    if run.previous:
        env["previous"] = previous_status(run.previous, found)
    return env


def _team(r: TeamRollup) -> dict:
    return {"team": r.team, **r.counts(), "headline": r.headline(), "capped": r.capped,
            "shipped_keys": [i.key for i in r.shipped], "in_flight_keys": [i.key for i in r.in_flight],
            "blocked_keys": [i.key for i in r.blocked], "next_week_keys": [i.key for i in r.next_week]}


def _unknown_share(run: Run) -> float:
    if not run.issues:
        return 1.0
    ctx = run.ctx or reality.Ctx(run.window.today)
    return sum(1 for i in run.issues if reality.category(i, ctx) == st.UNKNOWN) / len(run.issues)
