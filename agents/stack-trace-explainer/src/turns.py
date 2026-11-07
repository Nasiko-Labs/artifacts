"""The graph's node bodies: what each route does with the stored state and this turn's input.

Stored per conversation (checkpointer): the parsed traces, the primary index, the code blocks, the user's words
and the last envelope, so follow-ups reuse the parse instead of redoing it."""

from __future__ import annotations

import json

import checks
import envelope as envelope_mod
import explain as explain_mod
import frames as frames_mod
import hitl
import intake
import llm
import prompts
import render
import smalltalk
import trace_parse
from inputs import Incoming, fence
from paste import CodeBlock, Paste
from snippets import tie
from trace_model import Trace


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: llm.TurnDeps, incoming: Incoming, forced: intake.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake.Route | None = None


def route(state: dict, scope: Scope) -> dict:
    chosen = scope.forced or intake.plan_turn(scope.incoming, bool(state.get("envelope")))
    scope.route = chosen
    if chosen.kind == "reply":
        return {"route": "", "reply": chosen.reply}
    if chosen.kind == "json":
        env = state.get("envelope") or {}
        return {"route": "", "reply": render.render_json(env), "data": env}
    return {"route": chosen.kind}


async def explain_paste(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT_REPLY}
    pasted: Paste = scope.route.paste
    traces = [trace_parse.parse(t, scope.route.trust_libraries) for t in pasted.traces]
    results = await explain_all(scope, traces, pasted.blocks, pasted.words, pasted.primary)
    early = _early_reply(results[pasted.primary], pasted)
    if early is not None:
        return early
    env = envelope_mod.build(results, pasted.primary, pasted.skipped_traces)
    stored = {"traces": [t.to_dict() for t in traces], "primary": pasted.primary,
              "blocks": [b.to_dict() for b in pasted.blocks], "words": pasted.words}
    return {**stored, **_result(env, scope, (*pasted.notes, *_snippet_notes(results)))}


async def explain_all(scope: Scope, traces: list[Trace], blocks: tuple[CodeBlock, ...], words: str,
                      primary: int) -> list[explain_mod.Explained]:
    """Primary first. A trace gets a repair call only if every later trace still keeps one call."""
    deps = scope.deps
    order = [primary, *(i for i in range(len(traces)) if i != primary)]
    results: dict[int, explain_mod.Explained] = {}
    for k, index in enumerate(order):
        remaining = len(order) - k - 1
        trace = traces[index]
        if deps.budget.left <= 0 or not deps.has_time():
            results[index] = explain_mod.Explained("skipped", trace, frames_mod.candidate(trace))
            continue
        may_repair = deps.budget.left - 2 >= remaining
        results[index] = await explain_mod.explain(deps, trace, blocks, words, scope.incoming.policy, may_repair)
    return [results[i] for i in range(len(traces))]


def _early_reply(primary: explain_mod.Explained, pasted: Paste) -> dict | None:
    if primary.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if primary.status == "not_a_trace":
        data = {"summary": "No error found in the paste.", "items": [], "confidence": "low",
                "needs_human": envelope_mod.not_a_trace_needs_human("\n".join(pasted.traces))}
        return {"reply": prompts.NOT_A_TRACE_REPLY, "data": data}
    if primary.status in ("failed", "skipped"):
        return {"reply": prompts.MODEL_FAILED_REPLY}
    return None


def _snippet_notes(results: list[explain_mod.Explained]) -> tuple[str, ...]:
    if any(v.numbered_from_one for r in results for v in r.views):
        return (("_Line numbers in your snippet start at 1 (no `# lines A-B` header), so they may not match the "
                "file._"),)
    return ()


def _result(env: dict, scope: Scope, notes: tuple[str, ...]) -> dict:
    notes = (*scope.incoming.notes, *notes)
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, notes)
    return {"envelope": env, "data": env, "reply": reply}


async def attach(state: dict, scope: Scope) -> dict:
    """Pasted code for the stored primary trace: re-explain it (one call), keep the parse."""
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT_REPLY}
    traces = [Trace.from_dict(t) for t in state.get("traces") or ()]
    if not traces:
        return {"reply": prompts.NO_RESULT_YET}
    primary = int(state.get("primary") or 0)
    blocks = (*(CodeBlock.from_dict(b) for b in state.get("blocks") or ()), *scope.route.blocks)
    result = await explain_mod.explain(scope.deps, traces[primary], blocks, state.get("words") or "",
                                       scope.incoming.policy, may_repair=True)
    early = _early_reply(result, Paste("", (traces[primary].raw,), blocks))
    if early is not None:
        return early
    old = state.get("envelope") or {}
    fresh = envelope_mod.build([result], 0, old.get("skipped_traces") or 0)
    item = {**fresh["items"][0], "trace_index": primary + 1}
    others = [i for i in old.get("items") or () if i.get("trace_index") != primary + 1]
    env = {**fresh, "items": [item, *others], "summary": fresh["summary"]}
    note = "_Re-explained with the code you pasted (the trace was not re-parsed)._"
    return {"blocks": [b.to_dict() for b in blocks], **_result(env, scope, (note, *_snippet_notes([result])))}


async def followup(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT_REPLY}
    traces = [Trace.from_dict(t) for t in state.get("traces") or ()]
    env = state.get("envelope") or {}
    if not traces or not env.get("items"):
        return {"reply": prompts.NO_RESULT_YET}
    trace = traces[int(state.get("primary") or 0)]
    blocks = tuple(CodeBlock.from_dict(b) for b in state.get("blocks") or ())
    cand = frames_mod.candidate(trace)
    views = tuple(tie(trace, blocks, cand.frame))
    stored = {k: v for k, v in env["items"][0].items() if k not in ("trace", "checks")}
    content = "\n\n".join([f"QUESTION:\n{fence(scope.incoming.text, 'QUESTION')}",
                           f"STORED RESULT:\n{fence(json.dumps(stored, ensure_ascii=False), 'RESULT')}",
                           f"TRACE:\n{fence(trace.raw[:explain_mod.TRACE_PROMPT_CHARS], 'TRACE')}",
                           "CODE:\n" + ("\n\n".join(fence(v.render(), 'CODE') for v in views) or "(none)")])
    messages = [prompts.system(prompts.FOLLOWUP_SYSTEM, scope.incoming.policy), {"role": "user", "content": content}]
    try:
        answer = await llm.call_json(scope.deps, messages)
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED_REPLY}
    if not isinstance(answer, dict) or answer.get("status") == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY if isinstance(answer, dict) else prompts.MODEL_FAILED_REPLY}
    text = answer.get("answer") if isinstance(answer.get("answer"), str) else ""
    cleaned, removed = checks.clean_prose(text, checks.CheckContext(trace, views, cand.frame))
    if not cleaned:
        return {"reply": "I couldn't answer that from the error and code in this conversation. Paste the code it "
                         "concerns and ask again."}
    return {"reply": render.render_followup(cleaned, removed)}


async def scope_check(state: dict, scope: Scope) -> dict:
    """No error text and no stored result: one classify call decides between a decline and "Paste the error"."""
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT_REPLY}
    messages = [prompts.system(prompts.SCOPE_SYSTEM, scope.incoming.policy),
                {"role": "user", "content": fence(scope.incoming.text, "MESSAGE")}]
    try:
        answer = await llm.call_json(scope.deps, messages)
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED_REPLY}
    if not isinstance(answer, dict) or answer.get("status") != "in_scope":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    pending = hitl.Pending(hitl.NEED_TRACE, scope.incoming.text[:hitl.REQUEST_MAX_CHARS], scope.incoming.wants_json)
    return {"pause": pending.to_metadata()}
