"""The read steps of an analysis turn: list incidents in the window, then read the logs and notes of the
candidate incidents. The model only drives the tools; code reads every fact out of the raw results in the
turn's ledger, so ids, numbers and links are verbatim.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

import prompts
import records
from noise import LogFacts, Params
from reader import StepRun, TurnReader
from records import Incident, LogEntry

log = logging.getLogger("alert_tuner.steps")

MAX_INCIDENTS = 500
INCIDENT_CALLS, INCIDENT_SECS = 5, 90.0
LOG_CALLS, LOG_SECS = 6, 150.0


@dataclass(frozen=True)
class Incidents:
    items: tuple[Incident, ...] = ()
    notes: tuple[str, ...] = ()
    ran: bool = False                # a tool for the step existed
    status: str = ""
    listed: bool = False             # some tool output was an incident list (possibly empty)


@dataclass(frozen=True)
class Logs:
    facts: dict[str, LogFacts] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


async def read_incidents(reader: TurnReader, params: Params) -> Incidents:
    now = reader.deps.now
    start = now - timedelta(days=params.window_days)
    run = await reader.run("incidents", prompts.incidents_system(start, now, params.service, now), "List them.",
                           INCIDENT_CALLS, INCIDENT_SECS)
    if run.status == "no_tools":
        return Incidents(ran=False, status=run.status)
    texts = [c.text for c in reader.ledger.of("incidents")]
    found = collect_incidents(texts, params, start, now)
    listed = any(records.has_incident_list(t) for t in texts)
    notes = tuple(n for n in (run.note,) if n)
    if len(found) > MAX_INCIDENTS:
        notes = (*notes, f"Only the newest {MAX_INCIDENTS} of {len(found)} incidents were analysed (cap).")
        found = sorted(found, key=lambda i: i.created, reverse=True)[:MAX_INCIDENTS]
    if run.status in ("timeout", "max_iterations", "deadline") and not run.note:
        notes = (*notes, "The incident list may be partial: the listing step stopped early.")
    return Incidents(tuple(found), notes, True, run.status, listed)


def collect_incidents(texts: Sequence[str], params: Params, start, end) -> list[Incident]:
    """Unique incidents created inside the window (and on the service, when one was named)."""
    by_id: dict[str, Incident] = {}
    for text in texts:
        for incident in records.incidents(text):
            created = records.parse_time(incident.created)
            if created is None or not (start <= created <= end):
                continue
            if params.service and params.service.casefold() not in incident.service.casefold():
                continue
            by_id.setdefault(incident.id, incident)
    return list(by_id.values())


async def read_logs(reader: TurnReader, candidates: Sequence[Incident]) -> Logs:
    if not candidates:
        return Logs()
    ids = ", ".join(i.id for i in candidates)
    run = await reader.run("log", prompts.log_system(ids, reader.deps.now), "Read them.", LOG_CALLS, LOG_SECS)
    facts = collect_logs([(c.arguments, c.text) for c in reader.ledger.of("log")], candidates)
    return Logs(facts, _log_notes(run, candidates, facts))


def collect_logs(calls: Sequence[tuple[dict, str]], candidates: Sequence[Incident]) -> dict[str, LogFacts]:
    """Log facts per candidate: a call names the incident in its arguments (or its entries name it)."""
    known = {i.id for i in candidates}
    read: set[str] = set()
    entries: dict[str, list[LogEntry]] = {}
    notes: dict[str, int] = {}
    for arguments, text in calls:
        named = [x for x in records.ids_in(arguments) if x in known]
        target = named[0] if len(named) == 1 else ""
        for entry in records.log_entries(text, target):
            if entry.incident_id in known:
                entries.setdefault(entry.incident_id, []).append(entry)
                read.add(entry.incident_id)
        if target and records.has_log_list(text):
            read.add(target)
        if target and records.is_notes_list(text):
            notes[target] = notes.get(target, 0) + records.notes(text)
    return {i: LogFacts(read=i in read, entries=tuple(entries.get(i, ())), notes=notes.get(i, 0)) for i in known}


def _log_notes(run: StepRun, candidates: Sequence[Incident], facts: dict[str, LogFacts]) -> tuple[str, ...]:
    unread = sum(1 for i in candidates if not facts[i.id].read)
    notes = [run.note] if run.note else []
    if unread:
        notes.append(f"{unread} of {len(candidates)} incident logs could not be read; those incidents count as "
                     "unknown, not as zero-action.")
    return tuple(notes)
