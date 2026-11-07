"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

import generate
import hitl
import intake as intake_mod
import optimize
import prompts
import render
import smalltalk
from inputs import Incoming
from llm import LlmError, TurnDeps, call_json

TOO_MANY_QUESTIONS = ("I still need to know {what}, but I've asked twice already. Add it to your question (for "
                      "example \"use orders.total\") and ask again.")
NO_CANDIDATES = "Your schema has nothing that looks like {what}. Add the column or table and ask again."
UNPARSED = ("That query doesn't parse as {dialect}: {error}. Fix it, or name the dialect (for example "
            "\"dialect: bigquery\").")


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, asked: int = 0):
        self.deps, self.incoming, self.asked = deps, incoming, asked
        self.route: intake_mod.Route | None = None


def dialect_state(choice) -> dict:
    return {"name": choice.name, "assumed": choice.assumed, "how": choice.how}


def done(env: dict, scope: Scope, extra: dict | None = None) -> dict:
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, scope.incoming.text)
    return {"envelope": env, "data": env, "reply": reply, **(extra or {})}


def _pause(pending: hitl.Pending, route: intake_mod.Route) -> dict:
    return {"pause": pending.to_metadata(), "schema": route.schema.to_dict(),
            **({"dialect": dialect_state(route.choice)} if route.choice else {})}


async def write(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    prior = state.get("envelope") or {}
    ask = generate.Ask(route.question, route.schema, route.choice, scope.incoming.policy,
                       prior.get("sql", "") if prior.get("mode") == "write" else "", scope.incoming.text)
    outcome = await generate.write(scope.deps, ask)
    keep = {"schema": route.schema.to_dict(), "dialect": dialect_state(route.choice)}
    if outcome.kind == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY, **keep}
    if outcome.kind == "model_failed":
        return {"reply": prompts.MODEL_FAILED, **keep}
    if outcome.kind in ("needs_column", "ambiguous_join"):
        return {**_ask(outcome, route, scope), **keep}
    env = outcome.envelope
    env["warnings"] = [*route.notes, *env["warnings"]]
    return done(env, scope, {**keep, "pending_write": "" if route.pending_write else state.get("pending_write", ""),
                              "question": route.question})


def _ask(outcome: generate.Outcome, route: intake_mod.Route, scope: Scope) -> dict:
    is_column = outcome.kind == "needs_column"
    what = f"which column holds {outcome.concept or 'that value'}" if is_column else \
        f"how {' and '.join(outcome.tables) or 'the tables'} join"
    if scope.asked >= hitl.MAX_PAUSES:
        return {"reply": TOO_MANY_QUESTIONS.format(what=what)}
    if not outcome.options:
        return {"reply": NO_CANDIDATES.format(what=outcome.concept or what)}
    wants, asked = scope.incoming.wants_json, scope.asked + 1
    if is_column:
        pending = hitl.column_question(route.question, outcome.concept, list(outcome.options), wants, asked)
    else:
        a, b = (list(outcome.tables) + ["?", "?"])[:2]
        pending = hitl.join_question(route.question, a, b, list(outcome.options), wants, asked)
    return {"pause": pending.to_metadata()}


async def run_optimize(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent and not route.lint_only:
        return {"reply": prompts.NO_TRACEPARENT}
    job = optimize.Job(route.question, route.query, route.plan, route.schema, route.choice, scope.incoming.policy,
                       route.lint_only)
    outcome = await optimize.run(scope.deps, job)
    keep = {"schema": route.schema.to_dict(), "dialect": dialect_state(route.choice)}
    if outcome.kind == "unparsed":
        return {"reply": UNPARSED.format(dialect=route.choice.label, error=outcome.error), **keep}
    if outcome.kind == "write_statement":
        return {"reply": prompts.WRITE_DECLINE, "pending_write": route.query, **keep}
    if outcome.kind == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY, **keep}
    if outcome.kind == "model_failed":
        return {"reply": prompts.MODEL_FAILED, **keep}
    env = outcome.envelope
    env["warnings"] = [*route.notes, *env["warnings"]]
    return done(env, scope, keep)


async def classify(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    try:
        data = await call_json(scope.deps, prompts.classify_messages(route.question, scope.incoming.policy),
                               max_tokens=30)
    except LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if not data or data.get("status") != "sql_request":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if state.get("envelope"):
        return {"reply": prompts.FOLLOWUP_HELP}
    return _pause(hitl.schema_question(route.question, scope.incoming.wants_json, scope.asked + 1), route)


def simple(state: dict, scope: Scope) -> dict:
    """Routes that need no call: canned replies, declines, a stored schema, a pause for the schema."""
    route = scope.route
    if route.kind == "reply":
        return {"reply": route.reply}
    if route.kind == "decline":
        return {"reply": prompts.WRITE_DECLINE, "pending_write": route.pending_write,
                "schema": route.schema.to_dict(), "dialect": dialect_state(route.choice)}
    if route.kind == "stored":
        return {"reply": "\n".join([route.reply, *route.notes]), "schema": route.schema.to_dict(),
                "dialect": dialect_state(route.choice)}
    if scope.asked >= hitl.MAX_PAUSES:
        return {"reply": prompts.NEED_SCHEMA}
    pending = hitl.schema_question(route.question, scope.incoming.wants_json, scope.asked + 1)
    return {**_pause(pending, route), "pending_write": route.pending_write or state.get("pending_write", "")}
