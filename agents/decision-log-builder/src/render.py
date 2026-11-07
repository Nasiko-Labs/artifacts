"""User-facing text, all built in code: the decision list, the approval card body, copyable text and the
output envelope (`confidence` and `needs_human` computed here, never by the model). Markdown only, no raw
HTML, no images; URLs only from tool output."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date

from entry import clean
from models import Decision, Doc


def short_date(iso: str) -> str:
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return iso or "unknown date"
    return f"{day.day} {day.strftime('%b')}"


def _link(url: str | None) -> str:
    return f" [link]({url})" if url and url.startswith(("http://", "https://")) and " " not in url else ""


def decision_line(d: Decision, logged: bool = False) -> str:
    kind = "(implied) " if d.implied else ""
    who = clean(d.decided_by) + (f" (proposed by {clean(d.proposer)})" if d.proposer else "")
    line = (f"{d.id}. {kind}{clean(d.decision)} - {who}, {short_date(d.decided_at)}, #{clean(d.channel)} - "
            f"\"{clean(d.quote)}\"{_link(d.link)}")
    if d.supersedes:
        line += f" - supersedes #{d.supersedes} ({short_date(d.supersedes_date or '')})"
    if d.duplicate_of:
        line += f" - already in the log (\"{clean(d.duplicate_of)}\")"
    if logged:
        line += " - logged"
    return line


def option_label(d: Decision) -> str:
    """The multi-select label: `n. decision - who, date`, at most 200 UTF-8 bytes."""
    kind = "(implied) " if d.implied else ""
    label = f"{d.id}. {kind}{clean(d.decision)} - {clean(d.decided_by)}, {short_date(d.decided_at)}"
    return label.encode("utf-8")[:200].decode("utf-8", errors="ignore").strip()


def doc_line(doc: Doc | None) -> str:
    if doc is None or doc.state == "none":
        return "Log doc: none chosen yet."
    if doc.state == "create":
        return f'Log doc: a new doc "{clean(doc.title)}" will be created when you approve.'
    return f'Log doc: "{clean(doc.title)}" (found).{_link(doc.link)}'


def decision_list(decisions: Sequence[Decision], window_days: int, doc: Doc | None, notes: Sequence[str] = (),
                  written: Sequence[str] = (), not_decided: int = 0, pasted: bool = False,
                  can_write: bool = True) -> str:
    dupes = sum(1 for d in decisions if d.duplicate_of)
    where = "in what you pasted" if pasted else f"in the last {window_days} days"
    noun = "decision" if len(decisions) == 1 else "decisions"
    head = f"**{len(decisions)} {noun} {where}" + (f" ({dupes} already in the log)**" if dupes else "**")
    lines = [decision_line(d, d.id in written) for d in decisions]
    parts = [head, "\n".join(lines)]
    if not_decided:
        parts.append(f"{not_decided} question(s) or proposal(s) left out: they are not decisions.")
    if any(d.implied for d in decisions):
        parts.append("Implied decisions come from agreement in a thread; check them before logging.")
    parts.append(doc_line(doc) + (' Say "log 1,3" or "log all new".' if can_write else ""))
    return "\n\n".join([*parts, *notes])


def card(block: str, tool: str, doc: Doc, creates: bool) -> str:
    target = (f'Creates a new document "{clean(doc.title)}" with this text' if creates
              else f'Appends this text to the end of "{clean(doc.title)}"')
    return f"{target} (via {tool}):\n\n```markdown\n{block}\n```"


def copyable(block: str, reason: str) -> str:
    return f"{reason}\n\nHere is the block to copy into your decision log:\n\n```markdown\n{block}\n```"


def confidence(report: Mapping, decisions: Sequence[Decision]) -> str:
    if report.get("pasted"):
        return "low"
    if (report.get("dropped") or report.get("coverage") or report.get("from_envelope")
            or any(d.implied for d in decisions)):
        return "medium"
    return "high"


def envelope(report: Mapping, decisions: Sequence[Decision], doc: Doc | None, written: Sequence[str],
             doc_link: str = "", *, awaiting: bool = False) -> dict:
    count = len(written)
    doc_state = doc.state if doc else "none"
    unresolved = doc is None or doc_state in ("none", "create") and count == 0
    implied_open = any(d.implied and d.id not in written for d in decisions) and not report.get("reviewed")
    summary = (f"{len(decisions)} decision{'s' if len(decisions) != 1 else ''} found "
               f"({sum(1 for d in decisions if d.duplicate_of)} already in the log); {count} written to the log.")
    items = [d.to_dict() for d in decisions]
    return {"summary": summary, "items": items, "confidence": confidence(report, decisions),
            "needs_human": awaiting or unresolved or implied_open, "decisions": items,
            "doc": {"title": doc.title if doc else None, "id": (doc.id or None) if doc else None,
                    "state": "created" if doc_state == "create" and count else doc_state},
            "written": {"count": count, "doc_link": doc_link or None},
            "coverage": list(report.get("coverage") or ())}


def as_json(data: Mapping) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"
