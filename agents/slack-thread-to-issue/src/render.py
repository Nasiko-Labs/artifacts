"""User-facing text, all built in code: the proposal list, the create and link-back cards' bodies, copyable
text, and the output envelope (`confidence` and `needs_human` computed here, never by the model). Markdown
only: no raw HTML, no images, and the only URLs are ones a tool returned or the user pasted."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from models import Finding, IssueDraft

AWAITING = "Awaiting approval."
DRY_RUN = "Dry run, nothing created."
COPY_ONLY = "Copy it into your tracker."


def clean(text: str) -> str:
    return " ".join((text or "").replace("<", "‹").replace(">", "›").split())


def _channel(finding: Finding) -> str:
    return f"#{clean(finding.channel)}" if finding.channel else "a thread"


def _thread_link(finding: Finding) -> str:
    return f" [thread]({finding.link})" if finding.link else ""


def _owner(finding: Finding) -> str:
    if not finding.owner_name:
        return ""
    return f' - {clean(finding.owner_name)}: "{clean(finding.owner_quote or "")}"'


def proposal_line(n: int, finding: Finding, draft: IssueDraft, status: str) -> str:
    team = f" in {clean(draft.team.name)}" if draft.team else ""
    return (f'{n}. {_channel(finding)} - "{clean(finding.action_quote)}"{_owner(finding)}{_thread_link(finding)} '
            f'-> proposed: "{clean(draft.title)}"{team}. {status}')


def tracked_line(n: int, finding: Finding) -> str:
    link = f" ({finding.tracked_url})" if finding.tracked_url else ""
    return f'{n}. Already tracked: {finding.tracked_key}{link} - {_channel(finding)} "{clean(finding.action_quote)}"'


def heading(ready: int, scanned: Mapping) -> str:
    noun = "thread" if ready == 1 else "threads"
    threads, channels = int(scanned.get("threads") or 0), len(scanned.get("channels") or ())
    if scanned.get("pasted"):
        return f"**{ready} {noun} ready for an issue (from the pasted thread)**"
    where = (f" (scanned {threads} thread{'s' if threads != 1 else ''} in {channels} "
             f"channel{'s' if channels != 1 else ''}, last {int(scanned.get('window_days') or 0)} days)")
    return f"**{ready} {noun} ready for issues{where if threads else ''}**"


def proposal_list(proposed: Sequence[tuple[Finding, IssueDraft]], tracked: Sequence[Finding], status: str,
                  scanned: Mapping, remaining: int = 0, notes: Sequence[str] = ()) -> str:
    lines = [proposal_line(n, f, d, status) for n, (f, d) in enumerate(proposed, start=1)]
    lines += [tracked_line(n, f) for n, f in enumerate(tracked, start=len(proposed) + 1)]
    parts = [heading(len(proposed), scanned), "\n".join(lines)]
    if remaining:
        parts.append(f'{remaining} more thread{"s" if remaining != 1 else ""} waiting: say "continue" for the next 5.')
    return "\n\n".join([*parts, *notes])


def issue_card(draft: IssueDraft, finding: Finding | None, tool: str) -> str:
    owner = (f' (suggested from the thread, not set: {clean(finding.owner_name)}, "{clean(finding.owner_quote or "")}")'
             if finding and finding.owner_name else "")
    lines = [f"Title: {draft.title}", f"Team: {draft.team.name if draft.team else 'unknown'}"]
    if draft.project:
        lines.append(f"Project: {draft.project.name}")
    lines += [f"Labels: {', '.join(x.name for x in draft.labels) or 'none'}", "Priority: not set",
              f"Due: {draft.due or 'not set'}", f"Assignee: none{owner}", f"Via: {tool}", "", draft.description]
    return "\n".join(lines)


def issue_card_plain(draft: IssueDraft) -> str:
    labels = ", ".join(x.name for x in draft.labels) or "none"
    return (f"Title: {draft.title}\nTeam: {draft.team.name if draft.team else 'unknown'}\nLabels: {labels}\n"
            f"Due: {draft.due or 'not set'}\n\n{draft.description}")


def copyable_issue(draft: IssueDraft, reason: str) -> str:
    return f"{reason} Here is the issue to copy:\n\n{issue_card_plain(draft)}"


def linkback_card(where: str, thread_ts: str, text: str, tool: str) -> str:
    return "\n".join([f"Where: {where}, as a reply in thread {thread_ts}", f"Via: {tool}", "", text])


def copyable_linkback(where: str, text: str, reason: str) -> str:
    return f"{reason} Here is the reply to paste in the thread in {where}:\n\n{text}"


def proposed_issue(finding: Finding, draft: IssueDraft | None, state: str) -> dict:
    """One ProposedIssue of the envelope."""
    tracked = ({"key": finding.tracked_key, "url": finding.tracked_url or ""} if finding.tracked_key else None)
    owner = {"name": finding.owner_name, "quote": finding.owner_quote} if finding.owner_name else None
    return {"id": finding.id, "thread_link": finding.link, "channel": finding.channel,
            "title": draft.title if draft else finding.title, "description": draft.description if draft else "",
            "team": draft.team.name if draft and draft.team else None,
            "project": draft.project.name if draft and draft.project else None,
            "labels": [x.name for x in draft.labels] if draft else [], "action_quote": finding.action_quote,
            "owner_hint": owner, "already_tracked": tracked, "state": state, "issue_key": None, "issue_url": None,
            "linked_back": False}


def confidence(report: Mapping) -> str:
    if report.get("pasted"):
        return "low"
    if (report.get("dropped") or not report.get("dup_check_ok", True) or report.get("team_guessed")
            or report.get("coverage")):
        return "medium"
    return "high"


def envelope(report: Mapping, issues: Sequence[Mapping], *, awaiting: bool) -> dict:
    failed = any(i.get("state") == "failed" for i in issues)
    made = sum(1 for i in issues if i.get("state") == "created")
    linked = sum(1 for i in issues if i.get("linked_back"))
    tracked = sum(1 for i in issues if i.get("already_tracked"))
    proposed = len(issues) - tracked
    scanned = {"channels": list(report.get("channels") or ()), "threads": int(report.get("threads") or 0),
               "window_days": int(report.get("window_days") or 0)}
    summary = (f"{proposed} thread{'s' if proposed != 1 else ''} ready for issues ({tracked} already tracked); "
               f"{made} issue{'s' if made != 1 else ''} created, {linked} link-back repl"
               f"{'ies' if linked != 1 else 'y'} posted.")
    return {"summary": summary, "items": list(issues), "confidence": confidence(report),
            "needs_human": awaiting or failed or bool(report.get("remaining")), "proposed_issues": list(issues),
            "scanned": scanned, "coverage": list(report.get("coverage") or ())}


def as_json(envelope_: Mapping) -> str:
    return "```json\n" + json.dumps(envelope_, ensure_ascii=False, indent=2) + "\n```"
