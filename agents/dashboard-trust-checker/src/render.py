"""Pure: every reply is rendered here, in code: markdown only, no raw HTML, no images; dashboard names, owners
and quotes come from the inventory or the chat search output and are escaped; links only from tool output."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date

import prompts
from models import Audit, Dashboard

MAX_LISTED = 10
_ESCAPE = re.compile(r"[|`*\[\]]")
_DROP = re.compile(r"[<>]")


def safe(text: str | None) -> str:
    """Inline-safe: one line, no markdown or HTML control characters."""
    return _ESCAPE.sub(lambda m: "\\" + m.group(0), _DROP.sub("", " ".join(str(text or "").split())))


def json_block(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, default=str) + "\n```"


def source_label(source: str) -> str:
    if source.startswith("sheet:"):
        return f'your sheet "{safe(source[6:])}"'
    return "the envelope you sent" if source == "envelope" else "your pasted table"


def when(day: date | None, today: date) -> str:
    if day is None:
        return "unknown"
    return f"{day.day} {day.strftime('%b')}" + ("" if day.year == today.year else f" {day.year}")


def report(audit: Audit, today: date) -> str:
    lines = [f"**{audit.summary} (data: {source_label(audit.source)})**"]
    if audit.truncated:
        lines.append(f"Only the first rows were read; {audit.truncated} more were not audited.")
    lines += _section("Fix first", audit.fix_first, today)
    lines += _section(f"Retire candidates ({len(audit.retire)})", audit.retire, today)
    review = tuple(d for d in audit.dashboards if d.action == "review")
    lines += _section(f"Review with the owner ({len(review)})", review, today)
    quoted = [d for d in audit.dashboards if d.complaints.latest_quote]
    if quoted:
        lines += ["", "Chat complaints:"] + [_complaint(d) for d in quoted[:MAX_LISTED]]
    lines += ["", *audit.coverage, f"Confidence: {audit.confidence}.", prompts.NOT_OPENED_NOTE]
    return "\n".join(line for line in lines if line is not None)


def _section(title: str, items: Sequence[Dashboard], today: date) -> list[str]:
    if not items:
        return []
    shown = [f"- {line(d, today)}" for d in items[:MAX_LISTED]]
    more = [f"- and {len(items) - MAX_LISTED} more"] if len(items) > MAX_LISTED else []
    return ["", f"{title}:", *shown, *more]


def line(d: Dashboard, today: date) -> str:
    viewers = "viewers unknown" if d.viewers is None else f"{d.viewers} viewer{'s' if d.viewers != 1 else ''}"
    refresh = ("" if d.status == "stale" else f", refresh {d.age_days} days ago" if d.age_days is not None
               and d.age_days > 30 else f", last refresh {when(d.last_refresh, today)}")
    owner = f", owner {safe(d.owner)}" if d.owner else ""
    priority = " (high priority)" if d.priority == "high" else ""
    return (f'"{safe(d.name)}" - {d.status}{priority} ({safe(d.reasons[0]) if d.reasons else "no detail"}), '
            f"{viewers}{refresh}{owner}")


def _complaint(d: Dashboard) -> str:
    link = f" ({d.complaints.link})" if d.complaints.link else ""
    return f'- "{safe(d.name)}": {d.complaints.count} complaint(s); latest: "{safe(d.complaints.latest_quote)}"{link}'


def why(d: Dashboard, today: date) -> str:
    flags = ", ".join(d.flags) if d.flags else "none"
    lines = [f'**"{safe(d.name)}" is {d.status}; proposed action: {d.action}.**', f"Flags: {flags}.", "Reasons:"]
    lines += [f"- {safe(r)}" for r in d.reasons]
    viewers = "unknown" if d.viewers is None else str(d.viewers)
    lines.append(f"Viewers: {viewers}; last refresh: {when(d.last_refresh, today)}"
                 + (f"; owner: {safe(d.owner)}" if d.owner else "") + ".")
    if d.complaints.latest_quote:
        link = f" ({d.complaints.link})" if d.complaints.link else ""
        lines.append(f'Latest chat complaint: "{safe(d.complaints.latest_quote)}"{link}')
    elif d.complaints.count:
        lines.append(f"{d.complaints.count} chat complaint(s) found.")
    lines.append(prompts.NOT_OPENED_NOTE)
    return "\n".join(lines)


def retire_list(audit: Audit, today: date) -> str:
    if not audit.retire:
        unused = [d for d in audit.dashboards if d.status == "unused"]
        head = "Nothing meets the retire rule (unused and also broken or stale)."
        if unused:
            head += " These have no viewers but still work, so ask the owner first:"
        return "\n".join([head, *[f"- {line(d, today)}" for d in unused[:MAX_LISTED]], prompts.NOT_OPENED_NOTE])
    lines = [f"**Retire candidates ({len(audit.retire)}), proposed only: nothing has been changed.**"]
    lines += [f"- {line(d, today)}; {'; '.join(safe(r) for r in d.reasons)}" for d in audit.retire[:MAX_LISTED]]
    lines.append(prompts.NOT_OPENED_NOTE)
    return "\n".join(lines)


def delete_declined(audit: Audit | None, today: date) -> str:
    if audit is None:
        return f"{prompts.DELETE_DECLINED_SHORT} {prompts.NEEDS_INVENTORY}"
    targets = [d for d in audit.dashboards if d.action == "retire" or d.status == "unused"]
    if not targets:
        return f"{prompts.DELETE_DECLINED_SHORT} Nothing in this audit is flagged as unused or for retirement."
    return "\n".join([prompts.DELETE_DECLINED, *[f"- {line(d, today)}" for d in targets[:MAX_LISTED]]])
