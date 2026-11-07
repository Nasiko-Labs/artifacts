"""User-facing text, all built in code: the request list, the create and reply cards' bodies, copyable text,
and the output envelope (`confidence` and `needs_human` computed here, never by the model)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from models import IssueDraft, Request
from textcheck import parse_day

AWAITING = "awaiting your approval"
DRY_RUN = "dry run, nothing created"
COPY_ONLY = "copy it into your tracker"


def _short_date(iso: str | None) -> str:
    day = parse_day(iso)
    return f"{day.day} {day.strftime('%b')}" if day else ""


def _clean(text: str) -> str:
    return " ".join((text or "").replace("<", "‹").replace(">", "›").split())


def proposal_line(n: int, request: Request, draft: IssueDraft, status: str) -> str:
    team = f" in {_clean(draft.team.name)}" if draft.team else ""
    due = f"due {_short_date(draft.due)}" if draft.due else "no due date"
    priority = f"priority {draft.priority}" if draft.priority else "no priority"
    return (f'{n}. "{_clean(draft.title)}" - {_clean(request.who)}, asked "{_clean(request.ask_quote)}" - '
            f"proposed issue{team}, {due}, {priority}. -> {status}")


def tracked_line(n: int, request: Request) -> str:
    link = f" ({request.tracked_url})" if request.tracked_url else ""
    how = "your reply already links it" if request.tracked_by == "reply" else "found in your tracker"
    return f'{n}. Already tracked: {request.tracked_key}{link} - "{_clean(request.subject)}" ({how})'


def request_list(proposed: Sequence[tuple[Request, IssueDraft]], tracked: Sequence[Request], status: str,
                 remaining: int = 0, notes: Sequence[str] = ()) -> str:
    total = len(proposed) + len(tracked)
    noun = "request" if total == 1 else "requests"
    head = f"**{total} work {noun} found" + (f" ({len(tracked)} already tracked)**" if tracked else "**")
    lines = [proposal_line(n, r, d, status) for n, (r, d) in enumerate(proposed, start=1)]
    lines += [tracked_line(n, r) for n, r in enumerate(tracked, start=len(proposed) + 1)]
    parts = [head, "\n".join(lines)]
    if remaining:
        parts.append(f'{remaining} more request{"s" if remaining != 1 else ""} waiting: say "continue" for the '
                     "next 5.")
    return "\n\n".join([*parts, *notes])


def issue_card(draft: IssueDraft, tool: str) -> str:
    lines = [f"Title: {draft.title}", f"Team: {draft.team.name if draft.team else 'unknown'}"]
    if draft.project:
        lines.append(f"Project: {draft.project.name}")
    lines += [f"Labels: {', '.join(x.name for x in draft.labels) or 'none'}",
              f"Priority: {draft.priority or 'not set'}", f"Due: {draft.due or 'not set'}",
              "Assignee: none (left unassigned)", f"Via: {tool}", "", draft.description]
    return "\n".join(lines)


def copyable_issue(draft: IssueDraft, reason: str) -> str:
    return f"{reason} Here is the issue to copy:\n\n{issue_card_plain(draft)}"


def issue_card_plain(draft: IssueDraft) -> str:
    labels = ", ".join(x.name for x in draft.labels) or "none"
    return (f"Title: {draft.title}\nTeam: {draft.team.name if draft.team else 'unknown'}\nLabels: {labels}\n"
            f"Priority: {draft.priority or 'not set'}\nDue: {draft.due or 'not set'}\n\n{draft.description}")


def reply_card(to_name: str, to: str, subject: str, body: str, tool: str) -> str:
    return "\n".join([f"To: {to_name or to} <{to}>", f"Subject: {subject}", f"Via: {tool}", "", body])


def copyable_reply(to: str, subject: str, body: str, reason: str) -> str:
    return f"{reason} Here is the reply to copy (to {to}):\n\nSubject: {subject}\n{body}"


def confidence(report: Mapping) -> str:
    if report.get("pasted"):
        return "low"
    if (report.get("dropped") or not report.get("dup_check_ok", True) or report.get("team_guessed")
            or report.get("coverage")):
        return "medium"
    return "high"


def envelope(report: Mapping, proposed: Sequence[IssueDraft], created: Sequence[Mapping],
             replies: Sequence[Mapping], *, awaiting: bool) -> dict:
    failed = any(c.get("state") == "failed" for c in created)
    made = sum(1 for c in created if c.get("state") == "created")
    sent = sum(1 for r in replies if r.get("state") == "sent")
    found = int(report.get("found") or 0)
    tracked = int(report.get("tracked") or 0)
    summary = (f"{found} work request{'s' if found != 1 else ''} found ({tracked} already tracked); "
               f"{made} issue{'s' if made != 1 else ''} created, {sent} repl{'ies' if sent != 1 else 'y'} sent.")
    return {"summary": summary, "items": [*created, *replies], "confidence": confidence(report),
            "needs_human": awaiting or failed or bool(report.get("remaining")),
            "issues_created": list(created), "replies": list(replies),
            "proposed": [d.to_dict() for d in proposed], "coverage": list(report.get("coverage") or ())}


def as_json(envelope_: Mapping) -> str:
    return "```json\n" + json.dumps(envelope_, ensure_ascii=False, indent=2) + "\n```"
