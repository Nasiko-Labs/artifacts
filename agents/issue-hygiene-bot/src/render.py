"""Pure: the human reply, rendered in code as markdown (no raw HTML, no images, links only from tool output).
The model writes none of it."""

from __future__ import annotations

from collections.abc import Mapping

import rules
from envelope import Result, dm_status
from records import Finding, Owner

_STATUS_TEXT = {"not_drafted": "", "no_chat_user": " (no chat user matched: copy only)", "drafted": " (not sent)",
                "sent": " (message sent)", "skipped": " (skipped)"}


def summary(result: Result) -> str:
    owners = len(result.owners)
    percent = round(100 * result.flagged / result.open_total) if result.open_total else 0
    return (f"Hygiene, {result.label}: {result.flagged} flagged of {result.open_total} open issues read ({percent}%). "
            f"{owners} owner{'s' if owners != 1 else ''}, {len(result.unassigned)} unassigned.")


def report(result: Result, outcomes: Mapping[str, str]) -> str:
    parts = [f"**{summary(result)}**"]
    if len(result.scores) > 1 or any(not s.uses_estimates for s in result.scores):
        parts.append("Team scores (flagged of open issues read): " + "; ".join(_score(s) for s in result.scores) + ".")
    for owner in result.owners:
        parts.append(_owner_block(owner, dm_status(owner, outcomes)))
    if result.unassigned:
        parts.append("**Unassigned (not messaged):** " + ", ".join(_ref(f) for f in result.unassigned))
    if result.notes:
        parts.append("Notes:\n" + "\n".join(f"- {n}" for n in result.notes))
    return "\n\n".join(parts)


def _score(score: rules.TeamScore) -> str:
    extra = "" if score.uses_estimates else ", team does not use estimates (no-estimate rule skipped)"
    return f"{score.team} {score.percent}% ({score.flagged} of {score.open_total}{extra})"


def _owner_block(owner: Owner, status: str) -> str:
    lines = [f"**{owner.name} ({owner.count})**{_STATUS_TEXT.get(status, '')}"]
    lines.extend(f"- {_ref(f)}: {rules.why(f)}" for f in owner.findings)
    return "\n".join(lines)


def _ref(finding: Finding) -> str:
    issue = finding.issue
    title = "" if issue.title == issue.id else f" {_plain(issue.title)}"
    key = f"[{issue.id}]({issue.url})" if issue.url else issue.id
    return f"{key}{title}"


def _plain(text: str) -> str:
    """Tool text shown as text: no markdown links, images or html can be formed from it."""
    return text.replace("[", "(").replace("]", ")").replace("<", "‹").replace(">", "›").replace("!", "")


def walk_report(results: Mapping[str, str], names: Mapping[str, str], notes: tuple[str, ...]) -> str:
    """The end of an approval walk: sent, skipped and never-reached owners, then each per-owner note."""
    def named(status: str) -> list[str]:
        return [names.get(k, k) for k, s in results.items() if s == status]

    lines = [f"Sent: {', '.join(named('sent')) or 'nobody'}.", f"Skipped: {', '.join(named('skipped')) or 'nobody'}."]
    not_reached = named("not_reached")
    if not_reached:
        lines.append(f"Not reached (walk cancelled): {', '.join(not_reached)}. Sent messages cannot be unsent.")
    failed = [k for k, s in results.items() if s in ("not_sent", "blocked", "denied")]
    if failed:
        lines.append(f"Not sent: {', '.join(names.get(k, k) for k in failed)}.")
    return "\n".join(lines) + ("\n\n" + "\n\n".join(notes) if notes else "")
