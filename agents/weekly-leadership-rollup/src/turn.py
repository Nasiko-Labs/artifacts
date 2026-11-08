"""Turn 1 (and every re-pull): tracker -> chat (or the pasted update) -> calendar -> reality check ->
compute -> render. Each step reads only its own source; every record is checked in code before any number
is computed. More than 8 visible teams and none named: the turn ends with one "Which teams?" pause."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import checks
import coverage as coverage_
import envelope
import linking
import prompts
import reality
import render
import reports
import rollup
import smalltalk
import steps
from channels import named_in
from dates import Window
from deps import TurnDeps
from discovery import RECOVER_META, Catalog, discover
from envelope import Run
from request import Request
from steps import StepResult

log = logging.getLogger("weekly_rollup.turn")


@dataclass(frozen=True)
class Outcome:
    reply: str
    data: dict | None = None
    report: str = ""                     # the rollup, kept for follow-ups; empty when nothing was built
    pause_teams: tuple[str, ...] = ()    # set: ask "Which teams?" with these options
    extra: dict = field(default_factory=dict)


async def run_rollup(deps: TurnDeps, req: Request, teams: tuple[str, ...] = ()) -> Outcome:
    catalog, failure = await discover(deps)
    if failure is not None:
        return Outcome(await _scope_checked(deps, req, failure))
    if not catalog.tools_for("tracker") and await _off_topic(deps, req):
        return Outcome(smalltalk.OFF_TOPIC_REPLY)  # no tracker step to judge scope: ask once, tool-less
    window = Window(deps.today, req.days)
    tracker = await _step(deps, catalog, "tracker", "tracker", prompts.tracker_system(window, teams), req.ask)
    if tracker.status == "off_topic":
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    issues, capped, visible = _tracker_records(tracker, teams)
    named = _named_teams(tracker, req, visible)
    chosen = teams or named[: checks.MAX_TEAMS]
    if not chosen and len(visible) > checks.MAX_TEAMS:
        return Outcome(prompts.TEAMS_QUESTION.format(n=len(visible)), pause_teams=visible)
    if chosen and not teams:
        issues, capped = checks.check_issues(tracker.answer, tracker.seen, chosen)
    shown = chosen or visible
    said = await _reported(deps, catalog, req, window, shown)
    items, chat_steps = said.items, said.steps
    calendar = await _step(deps, catalog, "calendar", "calendar", prompts.calendar_system(window), req.ask)
    events = checks.check_events(calendar.answer, calendar.seen, window) if calendar.status == "ok" else ()
    if any(s.status == "off_topic" for s in (*chat_steps, calendar)):
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    covered = _coverage(tracker, chat_steps, calendar, bool(req.paste))
    if any(s.status == "paused" for s in (tracker, calendar, *chat_steps)):
        return Outcome(prompts.READ_NEEDS_APPROVAL)
    if covered.state("tracker") != "ok":
        return Outcome(render.unverified(items, coverage_.notes(covered)))
    if not issues and not shown:
        return Outcome(prompts.NO_TEAMS + "\n\n" + "\n".join(coverage_.notes(covered)))
    return await _finish(deps, req, Run(
        window=window, coverage=covered, issues=issues, rollups=rollup.build(issues, window, shown, capped),
        events=rollup.launch_events(events), conflicts=said.conflicts, previous=req.previous, capped=capped,
        partial=tuple(s.source for s in (tracker, *chat_steps, calendar) if s.partial),
        skipped=tuple(s.source for s in (tracker, *chat_steps, calendar) if s.status == "skipped"),
        posts=said.posts, notes=said.notes, ctx=reality.Ctx(deps.today, req.stale_days, req.soon_days, req.status_map)), items)


async def _finish(deps: TurnDeps, req: Request, run: Run, items: list[dict]) -> Outcome:
    fallback = False
    if items:
        matched, pending = linking.by_code(items, run.issues)
        if pending:
            matched, fallback = await linking.by_model(lambda m: steps.ask_json(deps, m), prompts.link_messages,
                                                       matched, pending, lambda: not deps.past_deadline())
        check = reality.run(matched, run.issues, run.ctx, unreported=req.unreported)
        run = Run(**{**run.__dict__, "items": tuple(matched), "check": check, "fallback": fallback})
    caveats = (run.check or {}).get("caveats") or reality.caveats(run.issues)
    env = envelope.build(run, caveats)
    markdown = render.report(run, env)
    full = {**env, "rollup_markdown": markdown}
    return Outcome(render.as_json(full) if req.wants_json else markdown, full, markdown)


async def _scope_checked(deps: TurnDeps, req: Request, failure: str) -> str:
    """No usable tools: still decline an off-topic ask instead of talking about connectors."""
    if failure != prompts.NO_TOOLS or not deps.traceparent:
        return failure
    return smalltalk.OFF_TOPIC_REPLY if await _off_topic(deps, req) else failure


async def _off_topic(deps: TurnDeps, req: Request) -> bool:
    """One tool-less scope call. A failed call counts as in scope: the fixed replies that follow are safe."""
    try:
        verdict = await steps.ask_once(deps, prompts.scope_messages(req.ask))
    except Exception:
        log.exception("scope check failed")
        return False
    return verdict.strip().upper().startswith(prompts.OFF_TOPIC)


async def _step(deps: TurnDeps, catalog: Catalog, source: str, loop: str, system: str, query: str) -> StepResult:
    return await steps.run_step(deps, loop, catalog.tools_for(source), catalog.writes, system, query)


def _tracker_records(tracker: StepResult, teams: tuple[str, ...]):
    if tracker.status not in ("ok", "teams"):
        return (), (), ()
    issues, capped = checks.check_issues(tracker.answer, tracker.seen, teams)
    return issues, capped, checks.visible_teams(tracker.answer, tracker.seen, issues)


def _named_teams(tracker: StepResult, req: Request, visible: tuple[str, ...]) -> tuple[str, ...]:
    """Teams the user named: the model's list, kept only when the message says it and a visible team matches."""
    raw = tracker.answer.get("named_teams") if tracker.status in ("ok", "teams") else None
    ask = req.ask.casefold()
    by_name = {t.casefold(): t for t in visible}
    names = [by_name[n.strip().casefold()] for n in raw if isinstance(n, str) and n.strip()
             and n.strip().casefold() in ask and n.strip().casefold() in by_name] if isinstance(raw, list) else []
    mentioned = [t for t in visible if t.casefold() in ask]
    return tuple(dict.fromkeys([*names, *mentioned]))


@dataclass(frozen=True)
class Reported:
    items: list
    conflicts: tuple = ()
    steps: tuple = ()
    posts: int = 0
    notes: tuple = ()


async def _reported(deps: TurnDeps, catalog: Catalog, req: Request, window: Window, teams) -> Reported:
    """Report items from the pasted update when there is one, else from status-like chat channels."""
    if req.paste:
        answer = await steps.ask_json(deps, prompts.paste_messages(req.paste))
        items, conflicts = reports.from_paste(checks.check_paste(answer or {}, req.paste), req.paste)
        return Reported(items, tuple(conflicts), posts=len(items))
    listed = await _step(deps, catalog, "chat", "channels", prompts.channels_system(teams), req.ask)
    if listed.status != "ok":
        return Reported([], steps=(listed,))
    kept = checks.check_channels(listed.answer, listed.seen, teams, named_in(req.ask))
    if not kept:
        log.info("no status-like channels kept from %d listed", len(checks.items(listed.answer, "channels")))
        return Reported([], steps=(listed,), notes=(prompts.NO_CHANNELS_NOTE,))
    chat = await _step(deps, catalog, "chat", "chat", prompts.chat_system(window, kept), req.ask)
    posts = checks.check_posts(chat.answer, chat.seen, window, kept) if chat.status == "ok" else ()
    items, conflicts = reports.from_posts(posts)
    return Reported(items, tuple(conflicts), (listed, chat), len(posts))


def _coverage(tracker: StepResult, chat_steps: tuple[StepResult, ...], calendar: StepResult,
              pasted: bool) -> coverage_.Coverage:
    offered = {"tracker": tracker.offered, "calendar": calendar.offered,
               "chat": chat_steps[0].offered if chat_steps else ()}
    results = (tracker, *chat_steps, calendar)
    dropped = {k: v for r in results for k, v in r.dropped.items()}
    pairs = (("tracker", tracker), ("calendar", calendar), *(("chat", s) for s in chat_steps))
    overrides = {source: step.status for source, step in reversed(pairs) if step.status in ("skipped", "unreadable")}
    if tracker.status in ("ok", "teams") and not tracker.seen.text:
        overrides = {**overrides, "tracker": "unreadable"}  # an answer with no tool output read nothing
    for source, step in pairs:
        if step.status != "no_tool":
            continue
        if step.dropped:  # the model gave up after the tools refused: report why, for every tool of the step
            reason = next(iter(step.dropped.values()))
            dropped = {**dropped, **{t.name: dropped.get(t.name, reason) for t in step.offered}}
        else:
            offered = {**offered, source: ()}
    if pasted:
        overrides = {**overrides, "chat": "pasted"}
    return coverage_.assess(offered, dropped, overrides, frozenset({RECOVER_META}))
