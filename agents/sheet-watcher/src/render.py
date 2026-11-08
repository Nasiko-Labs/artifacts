"""Pure: the markdown reply and the event lines, rendered in code (no raw HTML, no images, no links the input
did not carry), and the fenced json form for workflow steps."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from models import Event

MAX_LINES = 25
_LEAD = {"date_overdue": "Overdue", "required_blank": "Blank", "state_change": "Changed",
         "date_approaching": "Due soon", "new_row": "New row", "removed_row": "Removed row"}
FIRST_RUN_WHY = "first check: changes show from the next one"
_CONFIDENCE_WHY = {"low": "pasted data or rows keyed by row number, so reordering can look like changes",
                   "medium": "see the notes above", "high": "full read, unique keys, compared with your last check"}


@dataclass(frozen=True)
class View:
    title: str
    events: tuple[Event, ...]
    rows: int
    first_run: bool
    since: str | None                 # the previous check's time, e.g. "4 Nov 10:12"
    rules_line: str
    notes: tuple[str, ...]
    coverage: tuple[str, ...]
    confidence: str
    hint: str = ""


def line(event: Event) -> str:
    """One event as one plain line, used in the reply and the alert."""
    label = f'"{_clean(event.row_label)}"'
    if event.type in ("new_row", "removed_row"):
        return f"{_LEAD[event.type]}: {label}"
    return f"{_LEAD[event.type]}: {label} {_clean(event.detail)}"


def markdown(view: View) -> str:
    shown = [e for e in view.events if e.type != "row_changed"]
    edited = [e for e in view.events if e.type == "row_changed"]
    fresh = [e for e in shown if not e.repeat]
    repeats = [e for e in shown if e.repeat]
    lines = [_heading(view, len(shown))]
    budget = MAX_LINES
    for e in fresh[:budget]:
        lines.append(f"- {line(e)}")
    budget -= min(len(fresh), budget)
    if repeats and budget > 0:
        lines.append("Still true from your last check:")
        lines.extend(f"- {line(e)}" for e in repeats[:budget])
    hidden = len(shown) - min(len(fresh), MAX_LINES) - min(len(repeats), max(budget, 0))
    if hidden > 0:
        lines.append(f"- ...and {hidden} more (all of them are in the JSON envelope; say \"as json\").")
    if edited:
        lines.append(f"Also {len(edited)} other row(s) edited without a status change.")
    footer = [view.rules_line, *view.notes, *view.coverage]
    if view.first_run:
        footer.append(prompts.FIRST_RUN_NOTE)
    why = FIRST_RUN_WHY if view.first_run and view.confidence == "medium" else _CONFIDENCE_WHY[view.confidence]
    footer.append(f"Confidence: {view.confidence} ({why}).")
    if view.hint and shown:
        footer.append(view.hint)
    lines.extend(f"\n{text}" for text in footer)  # one paragraph each
    return "\n".join(lines)


def _heading(view: View, count: int) -> str:
    title = _clean(view.title)
    noun = f"{count} event{'s' * (count != 1)}"
    if view.first_run:
        return f"**{title}: baseline saved ({view.rows} rows), {noun}**"
    if count == 0:
        return f"**{title}: no events since your last check ({view.since})**"
    return f"**{title}: {noun} since your last check ({view.since})**"


def copyable(text: str) -> str:
    return f"```\n{text}\n```"


def json_block(envelope: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False) + "\n```"


def summary(title: str, events: Sequence[Event], first_run: bool) -> str:
    counts: dict[str, int] = {}
    for e in events:
        counts[e.type] = counts.get(e.type, 0) + 1
    parts = ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in counts.items()) or "no events"
    lead = "baseline saved" if first_run else "since the last check"
    return f"{title} ({lead}): {parts}."


def _clean(text: str) -> str:
    """No raw HTML or markdown links reach the chat: angle and square brackets are shown as text."""
    return (str(text).replace("<", "&lt;").replace(">", "&gt;").replace("[", "(").replace("]", ")")
            .replace("\n", " "))
