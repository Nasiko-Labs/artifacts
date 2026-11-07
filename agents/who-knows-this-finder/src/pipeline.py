"""One search, end to end:

    pasted rows → link + score (code) → reply                                  (no LLM, no tool call)
    people-ranking ask → decline                                               (no LLM, no tool call)
    keywords (LLM) → off-topic? decline : vague? ask (free text)
      → discover → code host (≤16 calls) → chat (≤8) + judge (≤2 LLM) → docs (≤4)
      → link + score (code) → more than 8 repositories? ask which : reply

A source that cannot be read is skipped with a coverage line; with none readable the reply asks for
pasted evidence and names nobody. The turn deadline is 300 s: later steps are skipped once it nears."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import chat_evidence
import code_evidence
import coverage
import doc_evidence
import keywords
import pasted
import prompts
import render
import smalltalk
import steps
from deps import TurnDeps
from discovery import PROFILE_QUERIES, discover
from finding import Gathered, build, repo_options
from keywords import Keywords
from models import SOURCES, Evidence, Who
from nasiko_hitl import question_metadata
from reader import BudgetExceeded, Reader, TurnBudget, ask_once
from request import Request, asks_who_knows, parse_request, ranks_people
from verify import normalise_text

log = logging.getLogger("who_knows.pipeline")

STEP_TIME_BUDGET_SECS = 200   # no new read step starts after this
JUDGE_TIME_BUDGET_SECS = 260  # no judge call starts after this (turn deadline 300 s)


@dataclass(frozen=True)
class Pause:
    kind: Literal["topic", "repo"]
    text: str
    hitl: dict | None = None
    topic: str = ""


@dataclass(frozen=True)
class FindOutcome:
    reply: str
    gathered: Gathered | None = None
    pause: Pause | None = None
    envelope: dict | None = None


async def find(deps: TurnDeps, text: str, budget: TurnBudget, *, allow_vague: bool = False,
               prior_topic: str = "") -> FindOutcome:
    rows = pasted.find(text)
    if rows is not None:
        return _from_pasted(deps, rows, text)
    req = parse_request(text)
    if ranks_people(req.topic):
        return FindOutcome(smalltalk.OFF_TOPIC_REPLY)
    if not deps.traceparent:
        return FindOutcome(prompts.NO_TRACE_CONTEXT)
    answer = keywords.parse_answer(await _keywords(deps, req.topic, budget), req.topic)
    if answer.off_topic and asks_who_knows(req.topic) and keywords.meaningful_words(req.topic):
        log.info("keyword step said off-topic for a plain who-knows question; using the user's own words")
        answer = keywords.parse_answer("", req.topic)
    if answer.off_topic or answer.keywords is None:
        return FindOutcome(smalltalk.OFF_TOPIC_REPLY)
    if keywords.is_vague(req.topic) and not allow_vague:
        if prior_topic and (req.repo or req.channel):  # "only in the payments repo": narrow the last topic
            return await find(deps, f"{prior_topic} {text}", budget, allow_vague=True)
        return FindOutcome("", pause=Pause("topic", prompts.VAGUE_QUESTION, None, req.topic))
    return await _search(deps, req, answer.keywords, budget)


async def _keywords(deps: TurnDeps, topic: str, budget: TurnBudget) -> str:
    messages = ({"role": "system", "content": prompts.KEYWORDS_SYSTEM}, {"role": "user", "content": topic})
    return await ask_once(deps, messages, budget)


async def _search(deps: TurnDeps, req: Request, kw: Keywords, budget: TurnBudget) -> FindOutcome:
    catalog, failure = await discover(deps, "read", PROFILE_QUERIES if req.exclude_me else ())
    if failure is not None:
        return FindOutcome(failure)
    reader = Reader.over(deps, catalog, budget)
    if not any(reader.has_source(s) for s in SOURCES):
        return FindOutcome(prompts.NO_SOURCE)
    gathered = await gather(deps, reader, req, kw)
    if not gathered.read_sources():
        return FindOutcome(_nothing_read(gathered))
    options = () if req.repo else repo_options(gathered)
    if options:
        hitl = question_metadata(prompts.REPO_HEADER, [(name, "") for name in options])
        return FindOutcome("", gathered, Pause("repo", prompts.REPO_HEADER, hitl))
    return reply_for(gathered)


def reply_for(gathered: Gathered) -> FindOutcome:
    finding = build(gathered)
    return FindOutcome(render.render(finding), gathered, envelope=render.envelope(finding))


async def gather(deps: TurnDeps, reader: Reader, req: Request, kw: Keywords) -> Gathered:
    statuses: dict[str, tuple[str, str]] = {}
    evidence: tuple[Evidence, ...] = ()
    dropped, salvaged, me = 0, False, None
    for source in SOURCES:
        if not reader.has_source(source):
            statuses = {**statuses, source: ("missing", "not connected")}
            continue
        if deps.elapsed() > STEP_TIME_BUDGET_SECS:
            statuses = {**statuses, source: ("skipped", prompts.TIME_SKIPPED)}
            continue
        answer = await steps.run(deps, reader, source, _system(deps, source, req, kw))
        if answer.data is None:
            statuses = {**statuses, source: (reader.status(source).state if reader.status(source).state != "ok"
                                             else "failed", answer.note)}
            continue
        items, lost, found_me = await _evidence(deps, reader, source, answer, kw)
        evidence, dropped, salvaged = (*evidence, *items), dropped + lost, salvaged or answer.salvaged
        me = me or found_me
        statuses = {**statuses, source: ("ok", "")}
    return Gathered(topic=req.topic, terms=kw.terms, evidence=evidence, statuses=statuses, as_of=deps.today,
                    dropped=dropped, salvaged=salvaged, exclude_me=req.exclude_me, me=me, repo=req.repo)


def _system(deps: TurnDeps, source: str, req: Request, kw: Keywords) -> str:
    if source == "code":
        return prompts.code_system(deps.now, kw.terms, kw.paths, req.repo, req.exclude_me)
    if source == "chat":
        return prompts.chat_system(deps.now, kw.terms, req.channel, req.exclude_me)
    return prompts.docs_system(deps.now, kw.terms, req.exclude_me)


async def _evidence(deps: TurnDeps, reader: Reader, source: str, answer: steps.StepAnswer,
                    kw: Keywords) -> tuple[tuple[Evidence, ...], int, Who | None]:
    me = code_evidence.who_of(answer.data.get("me"), normalise_text(answer.seen))
    if source == "code":
        read = code_evidence.evidence_from(answer.data, answer.seen, kw.terms)
        return read.evidence, read.dropped, me
    if source == "docs":
        read = doc_evidence.evidence_from(answer.data, answer.seen, kw.terms)
        return read.evidence, read.dropped, me
    found, dropped = chat_evidence.candidates(answer.data, answer.seen, kw.terms)
    if not found:
        return (), dropped, me
    if deps.elapsed() > JUDGE_TIME_BUDGET_SECS:
        log.info("no time left for the chat judge")
        return (), dropped, me
    try:
        judged = await chat_evidence.judge(deps, reader.budget, found)
    except BudgetExceeded:
        return (), dropped, me
    return judged.evidence, dropped + judged.dropped, me


def _nothing_read(gathered: Gathered) -> str:
    reasons = "; ".join(f"{coverage.LABELS[s]} ({gathered.statuses[s][1] or gathered.statuses[s][0]})" for s in SOURCES
                        if s in gathered.statuses)
    return (f"I couldn't read any of your sources this time ({reasons}), so I won't guess who knows it.\n\n"
            + prompts.PASTE_HINT)


def _from_pasted(deps: TurnDeps, rows: pasted.Pasted, text: str) -> FindOutcome:
    topic = rows.topic or "the pasted topic"
    terms = keywords.clean_terms(keywords.meaningful_words(topic))
    req = parse_request(topic)
    gathered = Gathered(topic=topic, terms=terms, evidence=rows.evidence, statuses={}, as_of=deps.today,
                        dropped=rows.skipped, mode="pasted", exclude_me=req.exclude_me)
    log.info("read %d pasted evidence row(s), skipped %d", len(rows.evidence), rows.skipped)
    return reply_for(gathered)
