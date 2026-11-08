"""The graph's working nodes: fetch, audit, follow-up and the scope call. Each returns a state update."""

from __future__ import annotations

import os
from typing import Any

import audit as audit_run
import followup as fu
import hitl
import prompts
import render
import rewrite
import smalltalk
import turns
from fetcher import Fetcher
from inputs import Incoming
from llm import LlmError, TurnDeps, complete_json
from models import Envelope

HTML_KEPT_CHARS = 30_000
TEXT_KEPT_CHARS = 20_000
CLASSES = ("off_topic", "needs_page", "about_audit")


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, resume: hitl.Pending | None = None):
        self.deps, self.incoming, self.resume = deps, incoming, resume
        self.plan: turns.Plan | None = None
        self.source: audit_run.Source | None = None


def fetch_enabled() -> bool:
    return os.environ.get("SEO_FETCH_ENABLED", "true").strip().casefold() not in ("false", "0", "no", "off")


async def fetch(scope: Scope) -> dict:
    req = scope.plan.request
    if not scope.deps.traceparent:
        return {"route": "", "reply": prompts.NO_TRACE_CONTEXT}
    if not fetch_enabled():
        return {"route": "", "reply": prompts.FETCH_DISABLED}
    result = await (scope.deps.fetcher or Fetcher()).fetch(req.url)
    if result.error:
        return {"route": "", "reply": prompts.fetch_failed(result.error)}
    notes = [f"Redirected to {result.final_url}." ] if result.redirects else []
    scope.source = audit_run.Source(html=result.html, page_url=result.final_url or req.url, keyword=req.keyword,
                                    robots_txt=result.robots_txt, llms_found=result.llms_found, fetched=True,
                                    notes=notes)
    return {"route": "audit"}


async def audit(scope: Scope, state: dict[str, Any]) -> dict:
    req = scope.plan.request
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    src = scope.source or audit_run.Source(html=req.html, page_url=req.url, keyword=req.keyword,
                                           truncated=req.truncated)
    env, text = await audit_run.run(scope.deps, src, scope.incoming.context)
    previous = state.get("last")
    if scope.plan.compare and previous:
        env = env.model_copy(update={"diff": fu.diff(previous.get("items") or [], [f.model_dump() for f in env.items])})
    data = env.model_dump(mode="json")
    reply = render.json_block(env) if req.json_mode else render.audit(env)
    return {"reply": reply, "envelope": data, "last": data, "text": text[:TEXT_KEPT_CHARS],
            "html": src.html[:HTML_KEPT_CHARS]}


async def followup(scope: Scope, state: dict[str, Any]) -> dict:
    env = Envelope.model_validate(state["last"])
    cmd = scope.plan.command
    if cmd.kind == "retitle":
        return await _retitle(scope, state, env, cmd.arg)
    if cmd.kind == "json":
        return {"reply": render.json_block(env), "envelope": state["last"]}
    if cmd.kind == "filter":
        return {"reply": render.filtered(env, fu.severities_from(cmd.arg))}
    if cmd.kind == "explain":
        return {"reply": render.explain(env, cmd.arg)}
    if cmd.kind == "robots":
        return {"reply": render.robots_view(env)}
    if cmd.kind == "geo":
        return {"reply": render.breakdown(env, "geo")}
    if cmd.kind == "breakdown":
        return {"reply": render.breakdown(env)}
    return {"reply": render.audit(env), "envelope": state["last"]}


async def _retitle(scope: Scope, state: dict[str, Any], env: Envelope, keyword: str) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    proposals = await rewrite.retitle(scope.deps, env.page, state.get("text") or "", state.get("html") or "",
                                      keyword, scope.incoming.context)
    shown = env.model_copy(update={"proposals": proposals})
    return {"reply": f"Title rewrite for **{keyword}**:\n\n{render.proposals(shown)}"}


async def classify(scope: Scope, state: dict[str, Any]) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    messages = prompts.classify_messages(scope.incoming.text, scope.incoming.context)
    try:
        data = await complete_json(scope.deps, messages, '{"status": "off_topic|needs_page|about_audit"}',
                                   lambda d: d if isinstance(d, dict) and d.get("status") in CLASSES else None,
                                   max_tokens=50)
    except LlmError:
        return {"reply": "I couldn't read that request this time. " + smalltalk.INTRO}
    status = data["status"]
    if status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if status == "needs_page" or not state.get("last"):
        if status == "about_audit":
            return {"reply": prompts.NO_AUDIT_YET}
        pending = hitl.Pending(keyword=scope.plan.request.keyword, instruction=scope.incoming.text[:300])
        return {"pause": pending.to_metadata()}
    env = Envelope.model_validate(state["last"])
    return {"reply": f"{render.header(env)}\n\n{env.summary}\n\n{prompts.FOLLOWUP_HELP}"}
