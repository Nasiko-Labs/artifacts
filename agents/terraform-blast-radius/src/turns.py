"""The graph's node bodies: intake (route a new message or a resumed answer), analyze, search_more.

Intake order: missing traceparent → fixed reply (zero calls); a pasted plan → analyze (or the one repository
question when no repository is known and no search output was pasted); a stored result → a follow-up read
in code, else one scope call (off topic → decline + intro); no plan → one scope call (off topic → decline +
intro, else the two commands to run).
"""

from __future__ import annotations

import logging
import re

import followup
import intake
import llm
import pipeline
import plan_parse
import prompts
import render
import smalltalk
from analysis import Analysis
from deps import TurnDeps
from envelope import build
from nasiko_hitl import question_metadata

log = logging.getLogger("terraform_blast_radius.turns")

SKIP_LABEL = "Skip: plan analysis only"
_SKIP = re.compile(r"^\s*(?:skip|none|no|nope|cancel|n/a|just the plan|plan analysis only)\b", re.IGNORECASE)


def reply(text: str, data: dict | None = None, **extra) -> dict:
    return {"reply": text, "data": data or {}, "route": "", **extra}


def result(a: Analysis, wants_json: bool) -> dict:
    text = render.as_json(a) if wants_json else render.markdown(a)
    return reply(text, build(a))


def repo_question() -> dict:
    return question_metadata(prompts.ASK_REPO_HEADER, [(SKIP_LABEL, "")], allow_custom_input=True)


async def intake_node(deps: TurnDeps, state: dict) -> dict:
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    if state.get("resume") is not None:
        return _resume(state)
    incoming = intake.Incoming(state.get("query") or "", tuple(state.get("files") or ()),
                               bool(state.get("wants_json")))
    request = intake.split(incoming)
    if request.plan_text:
        return _new_plan(state, request)
    stored = Analysis.from_dict(state.get("analysis"))
    if stored is not None:
        return await _followup(deps, stored, incoming)
    if plan_parse.looks_like_plan(incoming.text) or ("{" in incoming.text and len(incoming.text) > 200):
        return reply(prompts.NOT_A_PLAN)
    if await llm.is_off_topic(deps, incoming.text, has_result=False):
        return reply(smalltalk.OFF_TOPIC_REPLY)
    return reply(prompts.NO_PLAN)


def _new_plan(state: dict, request: intake.Request) -> dict:
    plan = plan_parse.parse(request.plan_text)
    if plan is None:
        return reply(prompts.NOT_A_PLAN)
    if not plan.changing:
        return reply(prompts.NO_CHANGES)
    repos = request.repos or ((state.get("repo"),) if state.get("repo") else ())
    job = {"plan_text": request.plan_text, "repos": list(repos), "env": request.env,
           "grep": [[h.path, h.line, h.fragments[0]] for h in request.grep], "wants_json": bool(state.get("wants_json"))}
    if not repos and not request.grep:
        return reply(prompts.ASK_REPO_TEXT, pending=job, pause=repo_question())
    return {"route": "analyze", "job": job, "pending": {}}


def _resume(state: dict) -> dict:
    pending = dict(state.get("pending") or {})
    if not pending.get("plan_text"):
        return reply(prompts.NOT_WAITING)
    answer = intake.current_message(state.get("resume") or "").strip()
    repos = intake.repos_in(answer) if not _SKIP.match(answer) else ()
    return {"route": "analyze", "job": {**pending, "repos": list(repos)}, "pending": {}}


async def _followup(deps: TurnDeps, stored: Analysis, incoming: intake.Incoming) -> dict:
    ask = followup.classify(incoming.text)
    if ask.kind == "search":
        return {"route": "search", "job": {"repos": list(ask.repos), "wants_json": incoming.wants_json}}
    if ask.kind == "owners":
        return reply(followup.owners(stored, ask.target), build(stored))
    if ask.kind == "why":
        return reply(followup.why(stored, ask.target), build(stored))
    if ask.kind == "destroys":
        return result(followup.destroys_only(stored), incoming.wants_json)
    if ask.kind == "no_tags":
        return result(followup.without_tags(stored), incoming.wants_json)
    if ask.kind == "json":
        return result(stored, True)
    if await llm.is_off_topic(deps, incoming.text, has_result=True):
        return reply(smalltalk.OFF_TOPIC_REPLY)
    return reply(prompts.FOLLOWUPS)


async def analyze_node(deps: TurnDeps, state: dict) -> dict:
    job = state.get("job") or {}
    plan = plan_parse.parse(str(job.get("plan_text") or ""))
    if plan is None:
        return reply(prompts.NOT_A_PLAN)
    repos = [str(r) for r in job.get("repos") or ()]
    env = intake.plan_env(str(job.get("env") or ""), plan.workspace)
    grep = tuple(intake.Hit(intake.PASTED, str(p), (str(t),), "", int(n)) for p, n, t in job.get("grep") or ())
    a = await pipeline.analyze(deps, plan, repos[0] if repos else "", repos[1:], env, grep)
    log.info("analysis: %d changes, %d searched, %d affected", len(a.changes), len(a.searched), len(a.affected))
    return {**result(a, bool(job.get("wants_json"))), "analysis": a.to_dict(), "repo": a.repo or state.get("repo")}


async def search_node(deps: TurnDeps, state: dict) -> dict:
    job = state.get("job") or {}
    stored = Analysis.from_dict(state.get("analysis"))
    if stored is None:
        return reply(prompts.NO_PLAN)
    a = await pipeline.search_more(deps, stored, [str(r) for r in job.get("repos") or ()])
    return {**result(a, bool(job.get("wants_json"))), "analysis": a.to_dict()}
