"""Pure: the reply (markdown, or the envelope as a fenced json block) and the envelope, both built in code from
the rows and the run meta. No raw HTML, no images; links only from tool output."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date

import coverage
import verdict as v
from config import LAUNCH_SOON_DAYS
from records import Row

_STATUS = {"pass": "pass", "warn": "warn", "fail": "FAIL", "unknown": "unknown"}
EVIDENCE_SHOWN = 5


def headline(launch: str, launch_date: date | None, today: date, rows: Sequence[Row], unread: Sequence[str]) -> str:
    result = v.verdict(rows, unread)
    ok, total = v.passed(rows)
    when = "no launch date given"
    if launch_date is not None:
        days = (launch_date - today).days
        when = f"launch {launch_date.isoformat()} ({days} day{'s' * (abs(days) != 1)}{' ago' if days < 0 else ''})"
    line = f"{launch}, {when}: {result.upper()} ({ok}/{total} checks pass)"
    if launch_date is not None and 0 <= (launch_date - today).days <= LAUNCH_SOON_DAYS and result != v.GO:
        line += f". Launch is in {(launch_date - today).days} days"
    return line


def _brief(rows: Sequence[Row]) -> str:
    return " | ".join(f"{r.rule_id} {r.detail}" for r in rows)


def render(launch: str, launch_date: date | None, today: date, rows: Sequence[Row], meta: v.RunMeta,
           statuses: Sequence[coverage.SourceStatus]) -> str:
    lines = [f"**{headline(launch, launch_date, today, rows, meta.unread)}**"]
    blocking = v.blockers(rows)
    warns = [r for r in rows if r.status == "warn"]
    if blocking:
        lines.append(f"Blockers: {_brief(blocking)}")
    if warns:
        lines.append(f"Warnings: {_brief(warns)}")
    unread = [coverage.line(s) for s in statuses if not s.ok]
    if unread:
        lines.append("Not read: " + " ".join(unread))
    lines += ["", "| Area | Rule | Status | Detail | Evidence |", "|---|---|---|---|---|", *(_row(r) for r in rows)]
    tail = [*meta.notes, "Every check and the verdict are computed in code. I'm read-only: I never edit anything."]
    return "\n".join([*lines, "", *tail]).strip()


def _row(r: Row) -> str:
    shown = list(r.evidence[:EVIDENCE_SHOWN])
    more = len(r.evidence) - len(shown)
    evidence = "; ".join(shown) + (f"; +{more} more" if more > 0 else "")
    blocking = " (blocking)" if r.blocking and r.status == "fail" else ""
    cells = (r.area, f"{r.rule_id} {r.title}", _STATUS[r.status] + blocking, r.detail, evidence or "-")
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def envelope(launch: str, launch_date: date | None, today: date, rows: Sequence[Row], meta: v.RunMeta) -> dict:
    level = v.confidence(meta)
    checklist = [r.to_dict() for r in rows]
    ok, total = v.passed(rows)
    return {
        "summary": headline(launch, launch_date, today, rows, meta.unread),
        "items": checklist,
        "confidence": level,
        "needs_human": v.needs_human(rows, meta, level),
        "checklist": checklist,
        "verdict": v.verdict(rows, meta.unread),
        "blockers": [r.to_dict() for r in v.blockers(rows)],
        "unknown_sources": list(meta.unread),
        "launch": launch,
        "launch_date": launch_date.isoformat() if launch_date else None,
        "checks_passed": ok,
        "checks_total": total,
        "notes": list(meta.notes),
    }


def as_json(data: dict) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n```"
