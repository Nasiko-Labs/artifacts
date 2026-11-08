"""Pure: the markdown report and the JSON envelope, both from a computed ScopeReport. Links come only from
tool output (each record's URL was checked against it); no raw HTML."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime

import prompts
import scope
from records import AddedItem, RemovedItem, ScopeReport

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|])")


def esc(text: str) -> str:
    """Tracker text shown as plain text: markdown and HTML characters escaped, one line."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join((text or "").split()))


def pts(value: float | None) -> str:
    if value is None:
        return "unestimated"
    return f"{int(value)}" if float(value).is_integer() else f"{value:.1f}"


def day(at: datetime) -> str:
    return f"{at:%b} {at.day}"


def pct_line(report: ScopeReport) -> str:
    if report.net_change_pct is None:
        return (f"Net change n/a (no estimates): {len(report.added)} added, {len(report.removed)} removed "
                f"against {report.baseline_count} committed.")
    sign = "+" if report.net_change_pct > 0 else ""
    rule = f"{int(scope.SCOPE_MODERATE_PCT)}/{int(scope.SCOPE_HIGH_PCT)} rule"
    return f"Net {sign}{report.net_change_pct:.1f} % ({report.severity} by the {rule})."


def markdown(report: ScopeReport, notes: Sequence[str] = (), since_last: str = "") -> str:
    s = report.sprint
    team = f", {esc(s.team)}" if s.team else ""
    lines = [f"**Scope change{team}, {esc(s.name)}** ({day(s.starts_at)} to {day(s.ends_at)})", ""]
    unest = f" ({report.baseline_unestimated} unestimated)" if report.baseline_unestimated else ""
    lines.append(f"Committed at start: {report.baseline_count} issues, {pts(report.baseline_points)} pts{unest}. "
                 f"Now: {report.now_count} issues, {pts(report.now_points)} pts. {pct_line(report)}")
    grace = (f"Grace period: additions within {report.grace_hours} h of the start count as planning."
             if report.grace_hours else "No grace period: every addition after the start is counted.")
    lines += [grace, ""]
    if since_last:
        lines += [since_last, ""]
    added_pts = sum(a.estimate or 0 for a in report.added)
    lines.append(f"**Added after start ({len(report.added)}, {pts(added_pts)} pts)**")
    lines += [f"- {_added_line(a)}" for a in report.added] or ["- none"]
    if report.removed:
        removed_pts = sum(r.estimate or 0 for r in report.removed)
        lines += ["", f"**Removed ({len(report.removed)}, {pts(removed_pts)} pts)**"]
        lines += [f"- {_removed_line(r)}" for r in report.removed]
    if report.in_grace:
        lines += ["", f"**Added during planning grace ({len(report.in_grace)}, not counted)**"]
        lines += [f"- {_link(a.issue_key, a.url)} {esc(a.title)}, added {day(a.added_at)} by {_who(a)}"
                  for a in report.in_grace]
    if report.added:
        lines += ["", "**Additions by person** (alphabetical)"]
        lines += [f"- {esc(p)}: {c} ({pts(v)} pts)" for p, c, v in report.by_person]
        lines.append(prompts.PERSON_FRAMING)
        lines += ["", "**Additions by day**: " + ", ".join(f"{d} ({n})" for d, n in report.by_day)]
    lines += ["", *[f"_{esc(n)}_" for n in (*_method_notes(report), *notes)], prompts.FRAMING_LINE]
    return "\n".join(lines).replace("\n\n\n", "\n\n")


def _added_line(a: AddedItem) -> str:
    bits = [f"{_link(a.issue_key, a.url)} {esc(a.title)} ({pts(a.estimate)} pts), added {day(a.added_at)} by {_who(a)}"]
    if a.from_backlog:
        bits.append("[from backlog]")
    if a.source == "created_after_start":
        bits.append("[created after start]")
    if a.late:
        bits.append("late")
    if a.likely_swap:
        bits.append("likely swap")
    if a.removed_at:
        bits.append(f"removed again {day(a.removed_at)}")
    return ", ".join(bits)


def _removed_line(r: RemovedItem) -> str:
    swap = ", likely swap" if r.likely_swap else ""
    return (f"{_link(r.issue_key, r.url)} {esc(r.title)} ({pts(r.estimate)} pts), moved out {day(r.removed_at)} "
            f"by {esc(r.removed_by)}{swap}")


def _who(a: AddedItem) -> str:
    if a.added_by == scope.UNKNOWN:
        why = "no actor in the history" if a.source == "history" else "creator not shown"
        return f"unknown ({why})"
    return f"automation: {esc(a.added_by)}" if a.by_automation else esc(a.added_by)


def _link(key: str, url: str) -> str:
    return f"[{esc(key)}]({url})" if url.startswith(("https://", "http://")) else esc(key)


def _method_notes(report: ScopeReport) -> list[str]:
    out = []
    if report.no_history:
        out.append(f"Not computed from history: {len(report.no_history)} issue(s) had no history "
                   f"({', '.join(report.no_history[:8])}), decided by creation date.")
    if report.undetermined:
        out.append(f"Left out: {', '.join(report.undetermined[:8])} (no history and no creation date).")
    if report.estimates_current and (report.added or report.baseline_count):
        out.append("Estimates are current values (no estimate history was available).")
    if report.sprint.ends_at and report.added and any(a.late for a in report.added):
        out.append("Late = added in the last 25 % of the sprint.")
    return out


def envelope(report: ScopeReport, notes: Sequence[str] = ()) -> dict:
    s = report.sprint
    added = [{"issue_key": a.issue_key, "title": a.title, "url": a.url, "estimate": a.estimate,
              "added_at": a.added_at.isoformat(), "added_by": a.added_by, "source": a.source,
              "assignee": a.assignee or None, "late": a.late, "likely_swap": a.likely_swap,
              "removed_at": a.removed_at.isoformat() if a.removed_at else None} for a in report.added]
    removed = [{"issue_key": r.issue_key, "title": r.title, "url": r.url, "estimate": r.estimate,
                "removed_at": r.removed_at.isoformat(), "removed_by": r.removed_by, "likely_swap": r.likely_swap}
               for r in report.removed]
    summary = (f"{s.name}: {len(report.added)} added, {len(report.removed)} removed after the start. "
               + pct_line(report))
    return {
        "summary": summary, "items": added, "confidence": report.confidence, "needs_human": report.needs_human,
        "added": added, "removed": removed,
        "baseline": {"count": report.baseline_count, "points": report.baseline_points},
        "net_change_pct": report.net_change_pct, "severity": report.severity,
        "sprint": {"id": s.id, "name": s.name, "team": s.team or None, "starts_at": s.starts_at.isoformat(),
                   "ends_at": s.ends_at.isoformat(), "url": s.url or None},
        "grace_hours": report.grace_hours,
        "coverage": [*_method_notes(report), *notes],
    }


def json_block(data: dict) -> str:
    return "```json\n" + json.dumps(data, indent=2) + "\n```"
