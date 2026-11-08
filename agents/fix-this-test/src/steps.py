"""The graph's model-backed steps: classify a message with no pasted data, run a fix, explain without
edits. Each returns state updates; the reply and the envelope are built by code."""

from __future__ import annotations

from dataclasses import dataclass

import checks
import diagnose
import envelope
import intake
import pipeline
import prompts
import render
from hitl import NEED_OUTPUT, Answer, Pending
from inputs import Constraints, Incoming
from llm import Budget, TurnDeps
from sandbox import Sandbox

RETRY_NOTE = "The user asked for a different approach. Do not repeat the previous patch:\n"
MAX_NOTE_PATCH_CHARS = 2_000


@dataclass
class Scope:
    """One turn's dependencies (never persisted)."""

    deps: TurnDeps
    budget: Budget
    sandbox: Sandbox
    incoming: Incoming
    resumed: tuple[Pending, Answer] | None = None


async def classify_step(state: dict, scope: Scope) -> dict:
    label = await diagnose.classify(scope.deps, scope.budget, scope.incoming.text, scope.incoming.org)
    if label == "unsupported_language":
        return intake.reply(prompts.UNSUPPORTED_LANGUAGE_REPLY)
    if label == "fix_request":
        rules = Constraints()
        pending = Pending(NEED_OUTPUT, "", {}, "", rules.to_dict(), scope.incoming.wants_json)
        return {"pending": pending.to_metadata(), "route": ""}
    if label in ("explain", "retry"):
        if not state.get("failure"):
            return intake.reply(prompts.NOTHING_STORED)
        return {"route": label}
    return intake.reply(prompts.OFF_TOPIC_REPLY)


def _note(state: dict) -> str:
    last = intake.stored_last(state)
    if state.get("route") == "retry" and last and last.get("patch"):
        return RETRY_NOTE + last["patch"][:MAX_NOTE_PATCH_CHARS]
    return state.get("note") or ""


async def fix_step(state: dict, scope: Scope) -> dict:
    sandbox = scope.sandbox
    if sandbox.lock.locked():
        return intake.reply(prompts.BUSY_REPLY)
    failure, files = state.get("failure") or "", dict(state.get("files") or {})
    req = pipeline.FixRequest(failure, files, Constraints.from_dict(state.get("rules")), scope.incoming.org,
                              state.get("static_reason"), _note(state))
    async with sandbox.lock:
        outcome = await pipeline.fix(scope.deps, scope.budget, sandbox, req)
    if outcome.loop is not None and outcome.loop.off_topic:
        return intake.reply(prompts.OFF_TOPIC_REPLY)
    coverage = list(state.get("coverage") or [])
    if outcome.flaky is not None:
        env = envelope.flaky(outcome, coverage)
        text = render.flaky_reply(env)
    else:
        explanation = outcome.loop.explanation if outcome.loop else ""
        if not explanation and outcome.loop and outcome.loop.finals != files and scope.budget.can_call():
            facts = [f"verified: {str(outcome.verified).lower()}", "The fix so far changed these files: "
                     + ", ".join(p for p in files if outcome.loop.finals.get(p) != files[p])]
            explanation = await diagnose.explain(scope.deps, scope.budget, "Explain the change that was made.",
                                                 facts, failure, outcome.loop.finals, scope.incoming.org) or ""
            explanation = "" if explanation == diagnose.OFF_TOPIC else explanation
        env = envelope.build(outcome, files, failure, explanation, coverage)
        text = render.fix_reply(env)
    reply = render.as_json(env) if state.get("wants_json") else text
    return {"reply": reply, "data": env, "last": env, "route": ""}


async def explain_step(state: dict, scope: Scope) -> dict:
    failure, files = state.get("failure") or "", dict(state.get("files") or {})
    last = intake.stored_last(state)
    facts, verified = [], False
    if last and last.get("patch") and not Constraints.from_dict(state.get("rules")).explain_only:
        verified = bool(last.get("verified"))
        facts = [f"verified: {str(verified).lower()}", "The last fix (a unified diff):", last["patch"][:4_000]]
    text = await diagnose.explain(scope.deps, scope.budget, scope.incoming.text, facts, failure, files,
                                  scope.incoming.org)
    if text == diagnose.OFF_TOPIC:
        return intake.reply(prompts.OFF_TOPIC_REPLY)
    evidence = failure + ("\n" + last["patch"] if last and last.get("patch") else "")
    kept, dropped = checks.check_explanation(text or "", files, evidence, set(), verified)
    return {"reply": render.explain_reply(kept, dropped), "route": ""}
