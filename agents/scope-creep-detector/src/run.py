"""Turn 1: (paste?) → discover → teams? → sprint → issues → history → snapshot. Each step's records are
checked in code; the method (scope.py) and the rendering (render.py) never see raw tool output."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pause
import prompts
import smalltalk
from checks import (
    Seen,
    check_events,
    check_issues,
    check_sprints,
    check_teams,
    select_sprint,
)
from deps import TurnDeps
from discovery import Catalog, discover
from paste import read_paste
from records import Snapshot
from request import ScopeRequest
from steps import StepResult, ask_once, run_step

log = logging.getLogger("scope_creep.run")

MAX_HISTORY_ISSUES = 120


@dataclass(frozen=True)
class PullOutcome:
    reply: str = ""
    snapshot: Snapshot | None = None
    notes: tuple[str, ...] = ()
    pause: Mapping | None = None  # {"text", "hitl", "metadata"} for the "Which team?" question


async def pull(deps: TurnDeps, req: ScopeRequest, team_override: str | None = None) -> PullOutcome:
    if not deps.traceparent:
        return PullOutcome(prompts.NO_TRACE_CONTEXT)
    if req.looks_pasted:
        return await _from_paste(deps, req)
    if deps.gateway is None:
        return await _no_tools(deps, req.text)
    catalog, failure = await discover(deps)
    if failure == "unreachable":
        return PullOutcome(prompts.GATEWAY_UNREACHABLE)
    if catalog is None:
        return await _no_tools(deps, req.text)
    return await _from_tracker(deps, req, catalog, team_override or req.team)


async def _from_tracker(deps: TurnDeps, req: ScopeRequest, catalog: Catalog, team: str | None) -> PullOutcome:
    query = req.text
    if team is None:
        teams = await _step(deps, catalog, "teams", prompts.teams_system(), query)
        stop = _stop(teams)
        if stop is not None:
            return stop
        names = check_teams(teams.answer, teams.seen) if teams.status == "ok" else ()
        if len(pause.team_labels(names)) > 1:
            return PullOutcome(pause=pause.team_pause(names, query))
        team = names[0] if names else None
    sprint_step = await _step(deps, catalog, "sprint", prompts.sprint_system(team, req.selector, deps.now), query)
    stop = _stop(sprint_step)
    if stop is not None:
        return stop
    sprints = check_sprints(sprint_step.answer, sprint_step.seen) if sprint_step.status == "ok" else ()
    sprint, others = select_sprint(sprints, req.selector, team, deps.now)
    if sprint is None and sprint_step.dropped:
        return PullOutcome(prompts.UNAVAILABLE.format(reasons=_reasons(sprint_step)))
    if sprint is None:
        which = {"current": "active", "last": "previous"}.get(req.selector, f"\"{req.selector}\"")
        return PullOutcome(prompts.NO_SPRINT.format(which=which, team=team or "your team"))
    issues_step = await _step(deps, catalog, "issues", prompts.issues_system(sprint.name, sprint.id, team), query)
    stop = _stop(issues_step)
    if stop is not None:
        return stop
    issues, over = check_issues(issues_step.answer, issues_step.seen) if issues_step.status == "ok" else ((), 0)
    if not issues and issues_step.dropped:
        return PullOutcome(prompts.UNAVAILABLE.format(reasons=_reasons(issues_step)))
    if not issues:
        return PullOutcome(prompts.NO_ISSUES.format(sprint=sprint.name))
    keys = frozenset(i.key for i in issues)
    embedded = check_events(issues_step.answer, issues_step.seen, keys, sprint.starts_at, deps.now)
    with_events = frozenset(e.issue_key for e in embedded)
    need = [i.key for i in issues if i.key not in with_events]
    asked, capped = need[:MAX_HISTORY_ISSUES], max(len(need) - MAX_HISTORY_ISSUES, 0)
    notes: list[str] = []
    events, history_keys, skipped, partial = embedded, with_events, False, issues_step.partial
    if asked and catalog.has_history:
        hist = await _step(deps, catalog, "history", prompts.history_system(sprint.name, sprint.id, asked), query,
                           asked)
        skipped = hist.status == "skipped"
        if hist.status == "ok":
            events = (*embedded, *check_events(hist.answer, hist.seen, keys, sprint.starts_at, deps.now))
            history_keys = with_events | _keys_in(asked, hist.seen)
        partial = partial or hist.partial
        if hist.partial:
            notes.append(prompts.STEP_LIMIT_NOTE.format(what="issue history"))
    missing = bool(asked) and not catalog.has_history and not with_events
    if missing:
        notes.append(prompts.NO_HISTORY_NOTE)
    if skipped:
        notes.append(prompts.HISTORY_SKIPPED_NOTE)
    if capped:
        notes.append(f"History was read for the first {MAX_HISTORY_ISSUES} issues only; {capped} not read.")
    if over:
        notes.append(f"The sprint lists more than {len(issues)} issues; {over} were left out.")
    if issues_step.partial:
        notes.append(prompts.STEP_LIMIT_NOTE.format(what="sprint's issues"))
    if others:
        notes.append(f"Also matched: {', '.join(others)}. Showing the most recent; name one to see it instead.")
    snapshot = Snapshot(sprint=sprint, issues=issues, events=tuple(events), history_keys=history_keys,
                        history_missing=missing, history_skipped=skipped, history_capped=capped,
                        other_matches=others, partial=partial)
    return PullOutcome(snapshot=snapshot, notes=tuple(notes))


async def _step(deps: TurnDeps, catalog: Catalog, step: str, system: str, query: str,
                replay_keys: Sequence[str] = ()) -> StepResult:
    return await run_step(deps, step, catalog.tools_for(step), catalog.writes, system, query, replay_keys)


def _stop(step: StepResult) -> PullOutcome | None:
    """The reply that ends the turn after a step, or None to go on."""
    if step.status == "off_topic":
        return PullOutcome(smalltalk.OFF_TOPIC_REPLY)
    if step.status == "paused":
        return PullOutcome(prompts.READ_NEEDS_APPROVAL)
    if step.status == "no_tool":
        return PullOutcome(prompts.NO_TOOLS_PASTE)
    if step.status in ("no_tools", "unreadable", "skipped") and step.step != "teams":
        reasons = _reasons(step)
        if reasons:
            return PullOutcome(prompts.UNAVAILABLE.format(reasons=reasons))
        if step.status == "skipped":
            return PullOutcome(prompts.TIME_LIMIT)
        return PullOutcome(prompts.UNREADABLE_TRACKER)
    return None


def _reasons(step: StepResult) -> str:
    return ", ".join(dict.fromkeys(step.dropped.values()))


def _keys_in(keys: Sequence[str], seen: Seen) -> frozenset[str]:
    return frozenset(k for k in keys if re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", seen.text))


async def _no_tools(deps: TurnDeps, text: str) -> PullOutcome:
    """No tracker: one tool-less call decides only whether the message is in scope, so an off-topic one
    still gets the decline rather than a setup note."""
    verdict = await ask_once(deps, ({"role": "system", "content": prompts.SCOPE_CHECK_SYSTEM},
                                    {"role": "user", "content": prompts.fence_paste(text)}))
    if verdict.strip().upper().startswith(prompts.OFF_TOPIC):
        return PullOutcome(smalltalk.OFF_TOPIC_REPLY)
    return PullOutcome(prompts.NO_TOOLS_PASTE)


async def _from_paste(deps: TurnDeps, req: ScopeRequest) -> PullOutcome:
    snapshot, failure = await read_paste(deps, req.text)
    if failure == "off_topic":
        return PullOutcome(smalltalk.OFF_TOPIC_REPLY)
    if snapshot is None:
        return PullOutcome(prompts.PASTE_UNREADABLE)
    return PullOutcome(snapshot=snapshot, notes=("Computed from the pasted list, not read from the tracker.",))
