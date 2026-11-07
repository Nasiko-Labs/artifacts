"""Follow-ups on the stored queue: code sentinels first (nudge #N, nudge all stale, why #N, only <repo>,
stale after N days, include drafts, who is overloaded, refresh), then one classifier call for anything
else. The stored snapshot is reused; the code host is read again only on "refresh"."""

from __future__ import annotations

from collections.abc import Mapping

import intake
import nudge
import prompts
import render
from ask import ask_once
from deps import TurnDeps
from queue_rules import Assessed
from turn import (
    Step,
    decline,
    read_and_render,
    render_load,
    render_queue,
    settings_from,
)
from view import Settings, Snapshot, View, build


async def followup_turn(deps: TurnDeps, text: str, settings: Settings, snapshot: Snapshot,
                        outcomes: Mapping[str, str]) -> Step:
    parsed = intake.parse(text)
    f = intake.followup(text, settings.repos)
    changed = settings_from(parsed, settings)
    if f.only:
        changed = changed.with_changes(only=f.only)
    elif intake.shows_all(text):
        changed = changed.with_changes(only="")
    if f.kind == "approval_word":
        return Step(prompts.NOTHING_PENDING)
    if f.kind in ("nudge", "nudge_all"):
        return await _nudge(deps, f, build(snapshot, changed, deps.now), snapshot, outcomes)
    if f.kind == "why":
        return _why(build(snapshot, changed, deps.now), f.number)
    if f.kind == "refresh":
        return await read_and_render(deps, changed.with_changes(only=""), parsed)
    if f.kind == "load":
        return _remembered(render_load(build(snapshot, changed, deps.now), parsed.as_json), changed)
    if f.kind == "queue" or changed != settings:
        return await render_queue(deps, changed, snapshot, parsed.as_json)
    label = await _classify(deps, text)
    if label == prompts.OFF_TOPIC:
        return decline()
    if label == prompts.LOAD:
        return render_load(build(snapshot, changed, deps.now), parsed.as_json)
    if label == prompts.REFRESH:
        return await read_and_render(deps, changed, parsed)
    if label == prompts.QUEUE:
        return await render_queue(deps, changed, snapshot, parsed.as_json)
    return Step(prompts.FOLLOWUP_HELP)


def _remembered(step: Step, settings: Settings) -> Step:
    return Step(step.reply, step.data, step.proposals, step.ask_repos, {"settings": settings.to_dict()})


async def _classify(deps: TurnDeps, text: str) -> str:
    raw = (await ask_once(deps, prompts.FOLLOWUP_SYSTEM, prompts.fence(text))).upper()
    for label in (prompts.OFF_TOPIC, prompts.LOAD, prompts.REFRESH, prompts.QUEUE, prompts.HELP):
        if label in raw.replace(" ", "_"):
            return label
    return prompts.HELP


def _find(v: View, number: int, by_rank_first: bool) -> Assessed | None:
    by_rank = v.stale[number - 1] if 1 <= number <= len(v.stale) else None
    by_number = next((a for a in v.in_view if int(a.facts.pr.number) == number), None)
    return (by_rank or by_number) if by_rank_first else (by_number or by_rank)


def _why(v: View, number: int) -> Step:
    a = _find(v, number, by_rank_first=False)
    return Step(render.why_markdown(a) if a else prompts.not_found(number))


async def _nudge(deps: TurnDeps, f: intake.Followup, v: View, snapshot: Snapshot,
                 outcomes: Mapping[str, str]) -> Step:
    if snapshot.pasted:
        return Step(prompts.NO_NUDGE_ON_PASTE)
    if not v.stale:
        return Step(prompts.no_stale(v.open_count, v.oldest_quiet) + " There is nothing to nudge.")
    if f.kind == "nudge_all":
        chosen = list(v.stale[:nudge.MAX_CARDS])
    else:
        a = _find(v, f.number, by_rank_first=True)
        if a is None or a not in v.stale:
            return Step(prompts.not_in_stale(f.number, len(v.stale)))
        chosen = [a]
    prepared = await nudge.prepare(deps, chosen, outcomes)
    preamble = ""
    if f.kind == "nudge_all" and len(v.stale) > nudge.MAX_CARDS:
        preamble = (f"{len(v.stale)} PRs are stale; here are the first {nudge.MAX_CARDS}, one card each. "
                    "Ask again for the rest.")
    return Step(prepared.reply or preamble, proposals=prepared.proposals)
