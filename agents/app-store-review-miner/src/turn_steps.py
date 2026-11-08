"""The graph's nodes (the turn's steps). Each takes the state and this turn's deps and returns the state
update. Model calls: the scope check and the follow-up (one each), plus whatever analyse / fetch spend."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import analyse
import checks
import inputs
import prompts
import render
import replies
import smalltalk
import steps_fetch
from deps import TurnDeps
from llm import LlmError, complete_json
from parse_reviews import parse
from records import review_from

log = logging.getLogger("review_miner.steps")


def _kind(raw: Any) -> dict | None:
    return dict(raw) if isinstance(raw, Mapping) and isinstance(raw.get("kind"), str) else None


def _done(reply: str) -> dict:
    return {"reply": reply, "route": ""}


def _merge(state: Mapping[str, Any], new: list[dict], ours: str | None) -> dict:
    """New reviews replace the stored reviews of the same apps; other apps are kept."""
    apps = {r["app"] for r in new}
    kept = [r for r in state.get("reviews") or [] if r["app"] not in apps]
    return {"reviews": [*kept, *new], "ours": state.get("ours") or ours or (new[0]["app"] if new else ""),
            "next_id": int(state.get("next_id") or 1) + len(new)}


def intake(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return _done(replies.NO_TRACE_CONTEXT)
    query = state["query"]
    env = inputs.input_envelope(query)
    if env is not None:
        report = render.markdown(env)
        return {**_done(render.as_json(env) if state.get("json_out") else report), "result": env, "report": report,
                "data": env}
    cut = len(query) > deps.config.paste_limit
    parsed = parse(query[:deps.config.paste_limit], start=int(state.get("next_id") or 1))
    if parsed.reviews:
        merged = _merge(state, [r.to_dict() for r in parsed.reviews], parsed.ours)
        if parsed.ours:
            merged["ours"] = parsed.ours
        return {**merged, "route": "analyse", "turn": {"skipped": parsed.skipped, "paste_cut": cut}}
    if state.get("result") and state.get("json_out"):
        return {**_done(render.as_json(state["result"])), "data": state["result"]}
    if state.get("result"):
        return {"route": "followup"}
    return {"route": "pull" if inputs.mentions_reviews(query) else "scope"}


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
    if answer["kind"] == prompts.NEEDS_PULL:
        return {"route": "pull"}
    text = str(answer.get("answer") or "").strip()
    if not text or not checks.numbers_ok(text, report):
        return _done(f"{replies.FOLLOWUP_UNVERIFIED}\n\n{report}")
    return _done(render.safe_block(text))


async def pull(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    fetched = await steps_fetch.fetch(deps, state["query"], int(state.get("next_id") or 1))
    if fetched.reply is not None:
        return _done(fetched.reply)
    merged = _merge(state, [r.to_dict() for r in fetched.reviews], None)
    return {**merged, "route": "analyse", "turn": {"coverage": list(fetched.coverage)}}


async def analyse_step(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    turn = state.get("turn") or {}
    intake_ = analyse.Intake(reviews=tuple(review_from(r) for r in state.get("reviews") or []),
                             cache=dict(state.get("cache") or {}), ours=state.get("ours") or "",
                             skipped=int(turn.get("skipped") or 0), paste_cut=bool(turn.get("paste_cut")),
                             coverage=tuple(turn.get("coverage") or ()))
    env, cache = await analyse.run(deps, intake_, state["query"])
    report = render.markdown(env)
    reply = render.as_json(env) if state.get("json_out") else report
    return {**_done(reply), "cache": cache, "result": env, "report": report, "data": env}
