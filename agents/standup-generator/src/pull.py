"""Turn 1: intake → window → discovery → (code host || tracker) → checks → render.

Every record is checked in code before anything is classified; the standup is rendered from the
stored records, so a follow-up can re-render it without reading the tools again.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

import coverage as coverage_
import envelope
import paste
import prompts
import render
import smalltalk
import steps
from checks import code_records, tracker_records
from deps import TurnDeps
from discovery import RECOVER_META, Catalog, discover
from envelope import Quality, as_json_text, wants_json
from intake import Request, read_request
from records import Records, to_json
from render import View
from report import Outcome, render_stored
from steps import StepResult
from window import Window, window_for

log = logging.getLogger("standup.pull")

PASTE_MIN_LINES = 3
PASTE_MIN_CHARS = 400


async def start(deps: TurnDeps, query: str, remembered: Request | None, view: View) -> Outcome:
    intake = await read_request(deps, query)
    if intake.status == "off_topic":
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    request = intake.request
    if not request.has_scope and remembered is not None:
        request = replace(request, repos=remembered.repos, project=remembered.project,
                          any_scope=remembered.any_scope)
    return await fetch(deps, request, window_for(intake.window_text, deps.now), query, view)


async def fetch(deps: TurnDeps, request: Request, window: Window, query: str, view: View,
                corrections: dict | None = None) -> Outcome:
    json_mode = wants_json(query)
    catalog, failure = await discover(deps)
    if failure is not None:
        if failure == prompts.NO_TOOLS and _pasted(query):
            return await pasted(deps, query, window, json_mode)
        return Outcome(failure)
    if not request.has_scope:
        if _pasted(query):
            return await pasted(deps, query, window, json_mode)
        return Outcome(prompts.SCOPE_QUESTION, pause={"request": request.to_dict(), "window": window.to_dict(),
                                                      "query": query, "view": view.to_dict()})
    code, tracker = await asyncio.gather(
        _step(deps, catalog, "code", prompts.code_system(window, *_scope(request)), query),
        _step(deps, catalog, "tracker", prompts.tracker_system(window, *_scope(request)), query))
    if "off_topic" in (code.status, tracker.status):
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    covered = _coverage(code, tracker)
    if covered.all_failed:
        return Outcome(coverage_.all_failed_reply(covered))
    if "paused" in (code.status, tracker.status):
        return Outcome(prompts.READ_NEEDS_APPROVAL)
    stored = _stored(request, window, code, tracker, covered, corrections or {})
    return await render_stored(deps, stored, view, json_mode, with_gist=True)


async def pasted(deps: TurnDeps, query: str, window: Window, json_mode: bool) -> Outcome:
    status, people = await paste.format_paste(deps, query)
    if status == "off_topic":
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    if not people:
        return Outcome(prompts.PASTE_EMPTY)
    view = View()
    markdown = render.standup(people, "your pasted notes", window, view)
    note = "Coverage: formatted from your pasted notes only; nothing was read from a tool."
    data = envelope.build(people, markdown, view, Quality(0, False, False, pasted=True), window.describe())
    reply = as_json_text(data) if json_mode else f"{markdown}\n\n{note}\n\n{render.READ_ONLY_NOTE}"
    return Outcome(reply, data)


def _pasted(query: str) -> bool:
    lines = [line for line in query.splitlines() if line.strip()]
    return len(lines) >= PASTE_MIN_LINES or len(query) >= PASTE_MIN_CHARS


def _scope(request: Request) -> tuple:
    return request.people, request.repos, request.project, request.everyone


async def _step(deps: TurnDeps, catalog: Catalog, source: str, system: str, query: str) -> StepResult:
    return await steps.run_step(deps, source, catalog.tools_for(source), catalog.writes, system, query)


def _coverage(code: StepResult, tracker: StepResult) -> coverage_.Coverage:
    results = (code, tracker)
    offered = {r.source: r.offered for r in results if r.status != "no_tool"}
    dropped = {k: v for r in results for k, v in r.dropped.items()}
    overrides = {r.source: r.status for r in results if r.status in ("skipped", "unreadable")}
    return coverage_.assess(offered, dropped, overrides, frozenset({RECOVER_META}))


def _stored(request: Request, window: Window, code: StepResult, tracker: StepResult,
            covered: coverage_.Coverage, corrections: dict) -> dict:
    from_code = code_records(code.answer, code.seen) if code.status == "ok" else Records()
    from_tracker = tracker_records(tracker.answer, tracker.seen) if tracker.status == "ok" else Records()
    records = Records(prs=from_code.prs, commits=from_code.commits, issues=from_tracker.issues,
                      cuts=(*from_code.cuts, *from_tracker.cuts))
    partial = [coverage_.LABEL[s.source] for s in (code, tracker) if s.partial]
    log.info("records: %d pull requests, %d commits, %d issues", len(records.prs), len(records.commits),
             len(records.issues))
    return {"request": request.to_dict(), "window": window.to_dict(), "fetched_since": window.since.isoformat(),
            "records": to_json(records), "coverage": list(coverage_.notes(covered)),
            "covered": sorted(covered.covered), "partial": partial, "corrections": corrections, "gists": []}
