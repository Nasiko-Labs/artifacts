"""One search turn: queries (model) → discover → search → shortlist → read threads → judge (model) →
verify → rank → envelope. Pasted threads skip the chat reads and go straight to the judge. The model
never answers the question: only quotes it chose and code verified reach the reply."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import judge
import prompts
import queries as queries_
import rank
import render
import search
import shortlist
import smalltalk
from deps import MAX_TOOL_CALLS, TurnDeps
from discovery import discover
from fence import CallBudget, Recorder
from models import Envelope, Hit, Message, Outcome, PriorAnswer, Searched, Thread, utc
from parse import QUERY_TEXT_CHARS, restate
from paste import NOTE as PASTE_NOTE
from readonly import ReadOnlyGateway
from terms import terms
from tool_health import ToolHealth
from verify import check

log = logging.getLogger("dedup.run")

@dataclass(frozen=True)
class Found:
    ordered: tuple[PriorAnswer, ...]
    threads: dict[str, list[dict]]
    dropped: int


async def run(deps: TurnDeps, question: str, channels: Sequence[str], months: int, truncated: bool,
              notes: Sequence[str] = ()) -> Outcome:
    q = await queries_.build(deps, question[:QUERY_TEXT_CHARS], truncated)
    if q.status == "off_topic":
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    catalog, failure = await discover(deps)
    if failure is not None:
        return Outcome(failure)
    health = ToolHealth(CallBudget(Recorder(ReadOnlyGateway(deps.gateway)), MAX_TOOL_CALLS))
    reach = await search.find_hits(deps, catalog, health, q.queries, channels, months)
    if not reach.available:
        return Outcome("\n\n".join([prompts.NO_CHAT_TOOL, *(f"_Note: {n}_" for n in reach.coverage)]))
    question_terms = terms(question[:QUERY_TEXT_CHARS])
    picked = shortlist.shortlist(shortlist.group(reach.hits), question_terms)
    read, read_notes = await search.read_threads(deps, catalog, health, picked)
    found = await judge_threads(deps, question, [t for t in read if t.replies], question_terms, pasted=False)
    labels = channels_of(reach.hits, channels)
    coverage = [*notes, *reach.coverage, *read_notes]
    if q.fallback:
        coverage.append("The search terms were built from your own words (the generated ones were unusable).")
    return outcome(deps, question, found, q.queries, Searched(channels=labels, months=months, hits=len(reach.hits)),
                   coverage, narrower=reach.narrower or bool(read_notes), pasted=False)


async def run_pasted(deps: TurnDeps, question: str, threads: Sequence[Thread]) -> Outcome:
    found = await judge_threads(deps, question, [t for t in threads if t.replies], terms(question[:QUERY_TEXT_CHARS]),
                                pasted=True)
    labels = sorted({t.channel_label for t in threads})
    note = PASTE_NOTE.format(n=len(threads), s="" if len(threads) == 1 else "s")
    return outcome(deps, question, found, [], Searched(channels=labels, months=0, hits=len(threads)), [note],
                   narrower=False, pasted=True)


async def judge_threads(deps: TurnDeps, question: str, threads: Sequence[Thread], question_terms: frozenset[str],
                        pasted: bool) -> Found:
    if not threads:
        return Found((), {}, 0)
    verdicts = await judge.judge(deps, question[:QUERY_TEXT_CHARS], threads)
    answers, dropped = [], 0
    by_key = {t.key: t for t in threads}
    for key, verdict in verdicts.items():
        candidate, why = check(verdict, by_key[key])
        if candidate is None:
            dropped += why in ("quote", "non_answer")
            continue
        answers.append(rank.build(candidate, question_terms, None if pasted else deps.now))
    ordered = rank.order(answers)
    stored = {a.thread_id: _messages(by_key[a.thread_id]) for a in ordered}
    log.info("judged %d threads: %d answers, %d dropped", len(threads), len(ordered), dropped)
    return Found(ordered, stored, dropped)


def outcome(deps: TurnDeps, question: str, found: Found, queries: Sequence[str], searched: Searched,
            coverage: Sequence[str], *, narrower: bool, pasted: bool) -> Outcome:
    shown = rank.select(found.ordered)
    if found.dropped:
        plural = "s" if found.dropped != 1 else ""
        coverage = [*coverage, (f"{found.dropped} candidate answer{plural} left out: the quote could not be found "
                                "word for word in the thread.")]
    env = Envelope(
        summary=summary(shown), items=list(shown), prior_answers=list(shown),
        confidence=rank.confidence(shown, narrower=narrower, dropped=found.dropped, pasted=pasted),
        needs_human=rank.needs_human(shown), question=question,
        restated_question=None if shown else restate(question), queries=list(queries), searched=searched,
        coverage=list(coverage)).model_dump()
    state = {"report": env, "ordered": [a.model_dump() for a in found.ordered], "threads": found.threads,
             "shown": [a.thread_id for a in shown]}
    return Outcome(render.report(env, deps.now.date()), env, state)


def summary(shown: Sequence[PriorAnswer]) -> str:
    if not shown:
        return "No prior answer found."
    strong = sum(1 for a in shown if a.match == "strong")
    return f"{len(shown)} prior answer{'s' if len(shown) != 1 else ''} found ({strong} strong)."


def channels_of(hits: Sequence[Hit], scoped: Sequence[str]) -> list[str]:
    if scoped:
        return [f"#{c}" for c in scoped]
    return list(dict.fromkeys(h.message.channel_label for h in hits))


def _messages(thread: Thread) -> list[dict]:
    return [{"author": m.author, "text": m.text, "when": _when(m)} for m in thread.messages[:50]]


def _when(message: Message) -> str:
    if message.time_label:
        return message.time_label
    stamp = utc(message.ts)
    return stamp.date().isoformat() if stamp else "unknown"
