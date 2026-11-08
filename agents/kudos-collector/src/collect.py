"""Turn 1: parse (code) → code host step → people → chat step → verify (code) → compute → render.
Each step reads only its own source; every record is checked in code before anything is counted."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import checks
import coverage as coverage_
import envelope
import kudos
import pasted as pasted_
import people as people_
import prompts
import render
import request as request_
import smalltalk
import steps
from deps import TurnDeps
from discovery import RECOVER_META, Catalog, discover
from records import PersonPacket
from steps import StepResult, ask_once

log = logging.getLogger("kudos_collector.collect")

CAPS_NOTE = "Volume cap reached for {who}: only the newest {what} are shown."
PEOPLE_CAP_NOTE = "{n} more people merged pull requests; I show the first {shown} by name. Name people to pick."
NAMED_CAP_NOTE = "I collect for at most 8 named people; {n} more were left out."
REVIEW_RULE_NOTE = "The tools returned no review-comment counts, so the review rule was not applied."
PASTE_ONLY_NOTE = "No code host or chat tool could be read; these packets show only what you pasted."
DIRECT_NOTE = "{n} direct or group direct messages were left out: I only use channel messages."
SCOPE_CHECK = (
    f"{prompts.SCOPE}\nReply with exactly IN_SCOPE if the user's message asks to collect evidence of people's work "
    f"(kudos, wins, praise, pull requests, promotion or review evidence), else exactly {prompts.OFF_TOPIC}."
)


@dataclass(frozen=True)
class Outcome:
    reply: str
    report: str = ""  # the packets, kept for follow-ups; empty when nothing was built
    envelope: dict | None = None


async def run(deps: TurnDeps, query: str) -> Outcome:
    if not deps.traceparent:
        return Outcome(prompts.NO_TRACE_CONTEXT)
    req = request_.parse(query, deps.today)
    catalog, failure = await discover(deps)
    if failure is not None:
        return await _without_tools(deps, query, req, failure)
    code = await _step(deps, catalog, "code", prompts.code_system(req.window, req.people, req.repos), query,
                       people_.named(req.people).replay_people())
    if code.status == "off_topic":
        if not request_.looks_in_scope(query):
            return Outcome(smalltalk.OFF_TOPIC_REPLY)
        log.info("code step said off_topic for a kudos-shaped request; treating it as unreadable")
        code = replace(code, status="unreadable")
    team = _people(req, query, code)
    records = checks.check_code(code.answer, code.seen, req.window, team.people) if code.status == "ok" \
        else checks.CodeRecords()
    team = people_.with_handles(team, records.authors)
    if not team.people:
        if code.status != "ok" and await _off_topic(deps, query):
            return Outcome(smalltalk.OFF_TOPIC_REPLY)
        failed = _coverage(code, None).failed
        return Outcome("\n\n".join([prompts.NO_PEOPLE, *(coverage_.line(s) for s in failed)]))
    chat = await _step(deps, catalog, "chat", prompts.chat_system(req.window, team.listing()), query,
                       team.replay_people())
    said = checks.check_chat(chat.answer, chat.seen, req.window, team.people) if chat.status == "ok" \
        else checks.ChatRecords()
    covered = _coverage(code, chat)
    paste = pasted_.parse(query, team.people)
    if covered.all_failed and not paste.any:
        return Outcome(coverage_.all_failed_reply(covered))
    if any(s.status == "paused" for s in (code, chat)):
        return Outcome(prompts.READ_NEEDS_APPROVAL)
    partial = tuple(s.source for s in (code, chat) if s.partial)
    notes = _notes(req, team, records, said, covered, partial)
    limited = bool(partial or records.capped or said.capped or team.left_out or req.people_left_out)
    return _finish(req, team, (*records.prs, *paste.prs), (*said.praise, *paste.praise), records.releases,
                   covered, notes, limited)


async def _without_tools(deps: TurnDeps, query: str, req: request_.Request, failure: str) -> Outcome:
    """No tool could be reached: pasted evidence still works; otherwise check scope, then the fixed reply."""
    team = people_.named(req.people)
    paste = pasted_.parse(query, team.people)
    if paste.any:
        return _finish(req, team, paste.prs, paste.praise, (), None, (PASTE_ONLY_NOTE,), True)
    return Outcome(smalltalk.OFF_TOPIC_REPLY if await _off_topic(deps, query) else failure)


async def _off_topic(deps: TurnDeps, query: str) -> bool:
    """One tool-less call, used only when no step could ask the model about scope; a kudos-shaped
    request needs no call."""
    if request_.looks_in_scope(query):
        return False
    verdict = await ask_once(deps, ({"role": "system", "content": SCOPE_CHECK}, {"role": "user", "content": query}))
    return verdict.startswith(prompts.OFF_TOPIC)


def _finish(req, team, prs, praise, releases, covered, notes, limited) -> Outcome:
    sources = covered.covered if covered else frozenset()
    packets = tuple(kudos.packet(p, prs, praise, releases, "code" in sources, "chat" in sources)
                    for p in _ordered(team))
    all_notes = (*notes, *([REVIEW_RULE_NOTE] if "code" in sources and kudos.review_data_missing(prs) else []))
    text = render.report(req.window, packets, sources, all_notes)
    data = envelope.build(req.window, packets, covered, limited, all_notes)
    return Outcome(text, text, data)


def _ordered(team: people_.People):
    return team.people if team.named else tuple(sorted(team.people, key=lambda p: p.label.casefold()))


def _people(req: request_.Request, query: str, code: StepResult) -> people_.People:
    if req.people:
        return people_.named(req.people)
    raw = code.answer.get("named_people") if code.status == "ok" else None
    names = request_.named_in_message(raw if isinstance(raw, list) else (), query.split("\n", 1)[0])
    if names:
        return people_.named(names)
    if code.status != "ok":
        return people_.People((), named=False)
    return people_.inferred(checks.authors_of(code.answer, code.seen))


def _notes(req, team, records, said, covered, partial) -> tuple[str, ...]:
    notes = list(coverage_.notes(covered))
    notes += [prompts.STEP_LIMIT_NOTE.format(source=coverage_.LABEL[s].lower()) for s in partial]
    notes += [CAPS_NOTE.format(who=w, what="150 pull requests") for w in records.capped]
    notes += [CAPS_NOTE.format(who=w, what="40 praise messages") for w in said.capped]
    if team.left_out:
        notes.append(PEOPLE_CAP_NOTE.format(n=team.left_out, shown=len(team.people)))
    if req.people_left_out:
        notes.append(NAMED_CAP_NOTE.format(n=req.people_left_out))
    if said.direct_dropped:
        notes.append(DIRECT_NOTE.format(n=said.direct_dropped))
    return tuple(notes)


async def _step(deps: TurnDeps, catalog: Catalog, source: str, system: str, query: str,
                replay_people=()) -> StepResult:
    return await steps.run_step(deps, source, catalog.tools_for(source), catalog.writes, system, query,
                                replay_people)


def _coverage(code: StepResult, chat: StepResult | None) -> coverage_.Coverage:
    results = [r for r in (code, chat) if r is not None]
    offered = {r.source: r.offered for r in results if r.status != "no_tool"}
    dropped = {k: v for r in results for k, v in r.dropped.items()}
    overrides = {r.source: r.status for r in results if r.status in ("skipped", "unreadable")}
    return coverage_.assess(offered, dropped, overrides, frozenset({RECOVER_META}))


__all__ = ["Outcome", "PersonPacket", "run"]
