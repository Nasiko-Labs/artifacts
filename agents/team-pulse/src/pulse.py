"""Turn 1: code host → tracker → (team) → chat → compute → render. Each step reads only its own
source; every record is checked in code before any number is computed."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import checks
import coverage as coverage_
import people as people_
import prompts
import render
import signals
import smalltalk
import steps
from deps import TurnDeps
from discovery import RECOVER_META, Catalog, discover
from records import Activity, Identity
from steps import StepResult
from windows import Window

log = logging.getLogger("team_pulse.pulse")


@dataclass(frozen=True)
class PulseOutcome:
    reply: str
    report: str = ""  # the cards, kept for follow-ups; empty when nothing was built


async def run_pulse(deps: TurnDeps, query: str) -> PulseOutcome:
    catalog, failure = await discover(deps)
    if failure is not None:
        return PulseOutcome(failure)
    window = Window(deps.today)
    code = await _step(deps, catalog, "code", prompts.code_system(window, ()), query)
    if code.status == "off_topic":
        return PulseOutcome(smalltalk.OFF_TOPIC_REPLY)
    named = _named(query, code)
    tracker = await _step(deps, catalog, "tracker", prompts.tracker_system(window, named), query,
                          [(n, {"label": n}) for n in named])
    if tracker.status == "off_topic":
        return PulseOutcome(smalltalk.OFF_TOPIC_REPLY)
    named = named or _named(query, tracker)
    prs, prs_capped = _ok(code, lambda a, s: checks.check_prs(a, s, window))
    commits, commits_capped = _ok(code, lambda a, s: checks.check_commits(a, s, window))
    issues, issues_capped = _ok(tracker, lambda a, s: checks.check_issues(a, s, window))
    identities = _identities(prs, commits, issues, window)
    team = people_.named_team(named, identities) if named else people_.inferred_team(identities)
    if not team.people:
        return PulseOutcome(_with_notes(prompts.NO_TEAM, _coverage(code, tracker, None).failed))
    chat = await _step(deps, catalog, "chat", prompts.chat_system(window, team.chat_listing()), query,
                       team.replay_people())
    chat_records = checks.check_chat(chat.answer, chat.seen, window, team.labels()) if chat.status == "ok" else ()
    covered = _coverage(code, tracker, chat)
    if covered.all_failed:
        return PulseOutcome(coverage_.all_failed_reply(covered))
    if any(s.status == "paused" for s in (code, tracker, chat)):
        return PulseOutcome(prompts.READ_NEEDS_APPROVAL)
    caps = tuple(what for what, hit in (("pull requests and reviews", prs_capped), ("commits", commits_capped),
                                        ("tickets", issues_capped)) if hit)
    activity = Activity(prs=prs, commits=commits, issues=issues, chat=chat_records, caps_hit=caps)
    team = team.with_zones({c.person: c.tz for c in chat_records if c.tz})
    partial = tuple(s.source for s in (code, tracker, chat) if s.partial)
    report = render.report(team, activity, window, signals.team_view(team, activity, window),
                           signals.cards(team, activity, window), covered, partial)
    return PulseOutcome(report, report)


async def _step(deps: TurnDeps, catalog: Catalog, source: str, system: str, query: str,
                replay_people=()) -> StepResult:
    return await steps.run_step(deps, source, catalog.tools_for(source), catalog.writes, system, query,
                                replay_people)


def _named(query: str, step: StepResult) -> tuple[str, ...]:
    raw = step.answer.get("named_people") if step.status == "ok" else None
    return people_.named_in_message(raw if isinstance(raw, list) else (), query)


def _ok(step: StepResult, check):
    return check(step.answer, step.seen) if step.status == "ok" else ((), False)


def _identities(prs, commits, issues, window: Window) -> list[Identity]:
    """Who was active in the current window, in record order."""
    found: list[Identity] = []
    for pr in prs:
        if window.in_window(pr.created_at):
            found.append(pr.author)
        found.extend(r.reviewer for r in pr.reviews if window.in_window(r.submitted_at))
    found.extend(c.author for c in commits if window.in_window(c.authored_at))
    found.extend(i.assignee for i in issues)
    return found


def _coverage(code: StepResult, tracker: StepResult, chat: StepResult | None) -> coverage_.Coverage:
    results = [r for r in (code, tracker, chat) if r is not None]
    offered = {r.source: r.offered for r in results if r.status != "no_tool"}
    dropped = {k: v for r in results for k, v in r.dropped.items()}
    overrides = {r.source: r.status for r in results if r.status in ("skipped", "unreadable")}
    return coverage_.assess(offered, dropped, overrides, frozenset({RECOVER_META}))


def _with_notes(reply: str, failed) -> str:
    lines = [coverage_.line(s) for s in failed]
    return "\n\n".join([reply, *lines])
