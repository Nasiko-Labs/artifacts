"""The graph's nodes (the turn's steps). Each takes the state and this turn's deps and returns the state
update. Model calls: the scope check and the follow-up (one each), plus whatever analyse / fetch spend."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import analyse
import inputs
import prompts
import render
import replies
import smalltalk
import steps_fetch
from deps import TurnDeps
from llm import LlmError, complete_json
from parse_blocks import parse
from records import block_from

log = logging.getLogger("change_watcher.steps")


def _kind(raw: Any) -> dict | None:
    return dict(raw) if isinstance(raw, Mapping) and isinstance(raw.get("kind"), str) else None


def _done(reply: str) -> dict:
    return {"reply": reply, "route": ""}


def _view(env: Mapping[str, Any], json_out: bool) -> str:
    return render.as_json(env) if json_out else render.markdown(env)


def intake(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return _done(replies.NO_TRACE_CONTEXT)
    query = state["query"]
    env = inputs.input_envelope(query)
    if env is not None:  # an envelope from an earlier step: shown, and kept for follow-ups
        return {**_done(_view(env, bool(state.get("json_out")))), "result": env,
                "report": render.markdown(env), "data": env}
    cut = len(query) > deps.config.paste_limit
    parsed = parse(query[:deps.config.paste_limit])
    if parsed.blocks:
        return {"route": "analyse", "turn": {"blocks": [b.to_dict() for b in parsed.blocks], "team": parsed.team,
                                             "paste_cut": cut}}
    if state.get("result") and state.get("json_out"):
        return {**_done(render.as_json(state["result"])), "data": state["result"]}
    if steps_fetch.url_in(query):
        return {"route": "fetch"}
    if state.get("result"):
        return {"route": "followup"}
    return _done(replies.FORMAT_HELP) if inputs.mentions_domain(query) else {"route": "scope"}


async def scope(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    messages = ({"role": "system", "content": prompts.SCOPE_SYSTEM}, {"role": "user", "content": state["query"]})
    try:
        answer = await complete_json(deps, messages, _kind)
    except LlmError as e:
        log.warning("scope check failed: %s", e)
        return _done(replies.FORMAT_HELP)
    return _done(smalltalk.OFF_TOPIC_REPLY if answer["kind"] == prompts.OFF_TOPIC else replies.FORMAT_HELP)


async def followup(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    report = state.get("report") or ""
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(report)}"
    messages = ({"role": "system", "content": system}, {"role": "user", "content": state["query"]})
    try:
        answer = await complete_json(deps, messages, _kind)
    except LlmError as e:
        log.warning("follow-up failed: %s", e)
        return _done(f"{replies.FOLLOWUP_UNVERIFIED}\n\n{report}")
    if answer["kind"] == prompts.OFF_TOPIC:
        return _done(smalltalk.OFF_TOPIC_REPLY)
    text = str(answer.get("answer") or "").strip()
    if not text or not render.numbers_ok(text, report):
        return _done(f"{replies.FOLLOWUP_UNVERIFIED}\n\n{report}")
    return _done(render.safe_block(text))


async def fetch(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    url = steps_fetch.url_in(state["query"]) or ""
    fetched = await steps_fetch.fetch(deps, url)
    if fetched.block is None:
        return _done(fetched.reply or replies.FORMAT_HELP)
    return {"route": "analyse", "turn": {"blocks": [fetched.block.to_dict()], "team": None, "paste_cut": False}}


async def analyse_step(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    turn = state.get("turn") or {}
    intake_ = analyse.Intake(blocks=tuple(block_from(b) for b in turn.get("blocks") or ()), team=turn.get("team"),
                             paste_cut=bool(turn.get("paste_cut")))
    env, snapshots = await analyse.run(deps, intake_, dict(state.get("snapshots") or {}))
    report = render.markdown(env)
    reply = render.as_json(env) if state.get("json_out") else report
    return {**_done(reply), "snapshots": snapshots, "result": env, "report": report, "data": env}
