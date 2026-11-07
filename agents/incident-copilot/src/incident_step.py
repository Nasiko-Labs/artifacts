"""The incident step: find the one incident (≤ 3 LLM calls, ≤ 60 s), check it against the alerting
output, and fix the start time T0. Several matches pause once for a pick; no alerting source (or no match)
falls back to the start time the user gave, asking for it once if they gave none."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import coverage as cov
import loops
import prompts
import records as r
import smalltalk
from deps import TurnDeps
from intake import Target
from investigation import Investigation, repo_from_url
from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from windows import CHANGE_BEFORE, Window, describe, iso, parse_time

log = logging.getLogger("incident_copilot.incident")

INCIDENT_CALLS = 3
MAX_CHOICES = 10


@dataclass(frozen=True)
class IncidentOutcome:
    reply: str | None = None                 # a fixed reply that ends the turn
    pause: dict | None = None                # {"kind", "text", "question", ...}: an input-required pause
    investigation: Investigation | None = None
    status: cov.SourceStatus | None = None   # the alerting source


def investigation_for(incident: r.Incident, target: Target, t0, deps: TurnDeps, alerting: bool) -> Investigation:
    service = incident.service or target.service
    repo = repo_from_url(incident.repo_url)
    repo_source = "incident" if repo else ("message" if target.repo else "")
    lookback = int(target.lookback.total_seconds() // 60) if target.lookback else 0
    return Investigation(incident=incident, t0=t0, now=deps.now, service=service, repo=repo or target.repo,
                         repo_source=repo_source, lookback_min=lookback, alerting=alerting)


def placeholder(target: Target, t0) -> r.Incident:
    name = target.service or target.incident_ref or "the service"
    return r.Incident(id=target.incident_ref, title=f"{name} incident", service=target.service, status="unknown",
                      created_at=iso(t0))


async def find_incident(deps: TurnDeps, target: Target, query: str) -> IncidentOutcome:
    reader = await loops.open_reader(deps, "incident")
    if reader.failure:
        return IncidentOutcome(reply=reader.failure)
    if not reader.tools:
        return _without_alerting(deps, target, query, cov.SourceStatus("alerting", "missing"))
    try:
        result = await asyncio.wait_for(_loop(deps, reader, target), timeout=deps.limits.incident_secs)
    except TimeoutError:
        log.warning("incident step timed out")
        status = cov.assess("alerting", reader.offered_names, reader.dropped, timed_out=deps.limits.incident_secs)
        return _without_alerting(deps, target, query, status)
    if r.answer_status(result) == "off_topic":
        return IncidentOutcome(reply=smalltalk.OFF_TOPIC_REPLY)
    seen, items = reader.haystack(), r.answer_list(result, "incidents") or []
    found = r.check_all(items, lambda i: r.check_incident(i, seen, deps.now)).kept
    found = _matching(found, target.incident_ref)
    status = cov.assess("alerting", reader.offered_names, reader.dropped, found_any=bool(found))
    if not found:
        return _without_alerting(deps, target, query, status)
    if len(found) == 1:
        incident = found[0]
        return IncidentOutcome(investigation=investigation_for(incident, target, parse_time(incident.created_at),
                                                               deps, True), status=status)
    return IncidentOutcome(pause=pick_pause(found[:MAX_CHOICES], query), status=status)


async def _loop(deps: TurnDeps, reader: loops.Reader, target: Target) -> str:
    recent = describe(Window("recent incidents", deps.now - CHANGE_BEFORE, deps.now))
    system = prompts.incident_system(f"Now is {iso(deps.now)}.", recent)
    request = f"Incident reference: {target.incident_ref or 'none'}\nService: {target.service or 'none'}"
    result = await loops.run(deps, reader, system, request, INCIDENT_CALLS, json_key="incidents")
    return result.text


def _matching(found: tuple[r.Incident, ...], ref: str) -> tuple[r.Incident, ...]:
    if not ref:
        return found
    exact = tuple(i for i in found if ref in (i.id, i.url) or (i.id and ref.rstrip("/").endswith(i.id)))
    return exact or found


def _without_alerting(deps: TurnDeps, target: Target, query: str, status: cov.SourceStatus) -> IncidentOutcome:
    if target.start is None:
        return IncidentOutcome(pause=start_pause(query, status), status=status)
    t0 = target.start
    return IncidentOutcome(investigation=investigation_for(placeholder(target, t0), target, t0, deps, False),
                           status=status)


def start_pause(query: str, status: cov.SourceStatus) -> dict:
    return {"kind": "ask_start", "text": prompts.ASK_START, "query": query, "alerting": status.to_dict(),
            "question": None}


def target_pause(query: str) -> dict:
    return {"kind": "ask_target", "text": prompts.ASK_TARGET, "query": query, "question": None}


def _label(incident: r.Incident, used: set[str]) -> str:
    base = " · ".join(p for p in (incident.title or incident.id, incident.service, incident.created_at) if p)
    label = base.encode("utf-8")[:MAX_OPTION_LABEL_LEN - 12].decode("utf-8", "ignore").strip()
    if label in used:
        label = f"{label[:MAX_OPTION_LABEL_LEN - 24]} ({incident.id})"[:MAX_OPTION_LABEL_LEN - 12]
    used.add(label)
    return label


def pick_pause(incidents: tuple[r.Incident, ...], query: str) -> dict:
    used: set[str] = set()
    choices = [{"label": _label(i, used), "incident": r.to_dict(i)} for i in incidents]
    question = question_metadata("Which incident should I investigate?",
                                 [(c["label"], f"Incident {c['incident']['id']}") for c in choices],
                                 multi_select=False, allow_custom_input=True)
    lines = [f"{n}. {c['label']}" for n, c in enumerate(choices, 1)]
    text = "I found several incidents. Which one should I investigate?\n" + "\n".join(lines)
    return {"kind": "pick_incident", "text": text, "query": query, "choices": choices, "question": question}


def chosen(pause: dict, answer: str) -> r.Incident | None:
    """The incident the answer names: its exact label, its number, or its id. Anything else is no choice."""
    text = (answer or "").strip()
    choices = pause.get("choices") or []
    for n, choice in enumerate(choices, 1):
        incident = choice.get("incident") or {}
        if text in (choice.get("label"), str(n), str(incident.get("id"))):
            return r.from_dict(r.Incident, incident)
    return None
