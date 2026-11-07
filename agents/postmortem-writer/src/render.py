"""Pure: the markdown postmortem, rendered by code from checked records. No raw HTML, no images, links only
when the URL is in the user's log (checked upstream). Views cost no LLM call."""

from __future__ import annotations

from collections.abc import Sequence

from checks import clean_text
from records import Action, Event, Postmortem

EMPTY = "None found."
VIEWS = ("default", "actions", "timeline", "factors", "shorter", "sources", "questions")
FACTOR_LABELS = {"trigger": "Trigger", "latent_condition": "Latent condition", "detection_gap": "Detection gap",
                 "response_gap": "Response gap", "process_gap": "Process gap", "tooling_gap": "Tooling gap"}
NO_CAUSE = "Cause not established from the log. The open questions below list what is missing."
SOURCE_LABELS = {"log": "log", "suggested": "suggested", "you": "added by you"}


def render(pm: Postmortem, view: str = "default", extra_notes: Sequence[str] = ()) -> str:
    sources = view == "sources"
    if view == "actions":
        body = [_actions(pm.actions, False)]
    elif view == "timeline":
        body = [_metrics(pm), _timeline(pm.events, False)]
    elif view == "factors":
        body = [_factors(pm, True)]
    elif view == "questions":
        body = [_list("Open questions", pm.questions)]
    elif view == "shorter":
        body = [_header(pm), _para("Summary", pm.summary), _metrics(pm), _actions(pm.actions, False)]
    else:
        body = [_header(pm), _para("Summary", pm.summary), _para("Impact", pm.impact), _metrics(pm),
                _timeline(pm.events, sources), _phase("Detection", pm.events, ("detection",)), _factors(pm, sources),
                _phase("Resolution", pm.events, ("mitigation", "resolution")),
                _list("What went well", pm.went_well), _list("What went poorly", pm.went_poorly),
                _list("Where we got lucky", pm.lucky), _actions(pm.actions, sources),
                _list("Open questions", pm.questions)]
    return "\n\n".join(part for part in (*body, footer(pm, extra_notes)) if part)


def footer(pm: Postmortem, extra_notes: Sequence[str] = ()) -> str:
    bits = [f"confidence: {pm.confidence}", "needs a human review" if pm.needs_human else "ready for review"]
    if pm.dropped:
        bits.append(f"{_count(pm.dropped)} left out: no supporting text found in your log")
    if pm.collapsed:
        bits.append(f"{pm.collapsed} log lines folded into timeline rows")
    notes = [n for n in (*pm.notes, *extra_notes) if n]
    return "\n".join([f"_({'; '.join(bits)}.)_", *notes])


def _count(n: int) -> str:
    return f"{n} item" if n == 1 else f"{n} items"


def _header(pm: Postmortem) -> str:
    date = f" ({pm.date})" if pm.date else ""
    severity = f"\n**Severity:** {pm.severity}" if pm.severity else ""
    return f"# Postmortem: {pm.title}{date}   _(draft: review before sharing)_{severity}"


def _para(name: str, text: str) -> str:
    return f"**{name}**\n{text or EMPTY}"


def _metrics(pm: Postmortem) -> str:
    m = pm.metrics
    zone = f"{pm.tz_label} as written" if pm.tz_label else "times as written"
    return (f"Time to detect: {m.get('time_to_detect', 'Not stated')} · time to mitigate: "
            f"{m.get('time_to_mitigate', 'Not stated')} · duration: {m.get('duration', 'Not stated')} ({zone})")


def _when(event: Event) -> str:
    if not event.time_text:
        return "-"
    return f"{event.time_text} {event.tz}" if event.tz and event.tz not in event.time_text else event.time_text


def _quote(snippet: str) -> str:
    return f'\n  > "{clean_text(snippet, snippet)}"'


def _timeline(events: Sequence[Event], sources: bool) -> str:
    if not events:
        return "**Timeline**\n" + EMPTY
    if sources:
        lines = [f"- {_when(e)} {e.text} ({e.category})" + _quote(e.snippet) for e in events]
        return "**Timeline**\n" + "\n".join(lines)
    rows = [f"| {_when(e)} | {e.text}{f' (+{e.collapsed} similar)' if e.collapsed else ''} | {e.category} |"
            for e in events]
    return "**Timeline**\n| Time | What happened | Category |\n|---|---|---|\n" + "\n".join(rows)


def _phase(name: str, events: Sequence[Event], categories: tuple[str, ...]) -> str:
    picked = [f"- {_when(e)}: {e.text}" for e in events if e.category in categories]
    return f"**{name}**\n" + ("\n".join(picked) or f"No {name.lower()} event found in the log.")


def _factors(pm: Postmortem, sources: bool) -> str:
    if not pm.factors:
        return "**Contributing factors**\n" + NO_CAUSE
    lines = []
    for n, f in enumerate(pm.factors, start=1):
        text = f"Likely: {f.text}" if f.inferred else f.text
        lines.append(f"{n}. {FACTOR_LABELS.get(f.cls, f.cls)}: {text} ({', '.join(f.event_ids)})"
                     + (_quote(f.snippet) if sources else ""))
    return "**Contributing factors**\n" + "\n".join(lines)


def _owner(action: Action) -> str:
    return f"{action.owner} (set by you)" if action.owner_set_by_you else action.owner


def _actions(actions: Sequence[Action], sources: bool) -> str:
    if not actions:
        return "**Action items**\n" + EMPTY
    if sources:
        lines = [f"- {a.id} {a.title} (owner: {_owner(a)}; due: {a.due}; {SOURCE_LABELS.get(a.source, a.source)})"
                 + (_quote(a.snippet) if a.snippet else "") for a in actions]
        return "**Action items**\n" + "\n".join(lines)
    rows = [f"| {a.id} | {a.title} | {_owner(a)} | {a.due} | {a.type} | {SOURCE_LABELS.get(a.source, a.source)} |"
            for a in actions]
    return "**Action items**\n| # | Action | Owner | Due | Type | Source |\n|---|---|---|---|---|---|\n" + "\n".join(rows)


def _list(name: str, items: Sequence[str]) -> str:
    return f"**{name}**\n" + ("\n".join(f"- {i}" for i in items) or EMPTY)
