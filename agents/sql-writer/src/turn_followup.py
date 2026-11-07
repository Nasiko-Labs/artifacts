"""Follow-up turns on the result stored in this conversation. All but "show the rows it would touch" are code only
(zero calls); that one becomes a write turn for the matching SELECT."""

from __future__ import annotations

import dialect as dialect_mod
import followup
import intake as intake_mod
import render
import turns
from schema import Schema

NOTHING_STORED = "There's no query in this conversation yet. Paste a schema and a question, or a query."


async def run(state: dict, scope: turns.Scope) -> dict:
    command = scope.route.command
    env = state.get("envelope") or {}
    if command.kind == "rows":
        choice = intake_mod.stored_choice(state) or dialect_mod.choose("")
        scope.route = intake_mod.rows_route(scope.route.pending_write, state, choice)
        if scope.route.kind == "pause_schema":
            return turns.simple(state, scope)
        return await turns.write(state, scope)
    if not env.get("sql"):
        return {"reply": NOTHING_STORED}
    if command.kind == "json":
        return {"reply": render.render_json(env), "data": env}
    if command.kind == "sql_only":
        return {"reply": render.render_sql_only(env), "data": env}
    if command.kind == "explain":
        text = render.clean(env.get("explanation") or "") or "No explanation was stored for this query."
        lines = "\n".join(f"{n}. {render.clean(a)}" for n, a in enumerate(env.get("assumptions") or [], 1))
        return {"reply": f"**What it does** {text}\n\n**Assumptions**\n{lines or render.NONE}", "data": env}
    if command.kind in ("pretty", "one_line"):
        return {"reply": render.sql_block(followup.reformat(env, command.kind == "pretty")), "data": env}
    new_env, error = followup.transpile(env, command.target, Schema.from_dict(state.get("schema")))
    if new_env is None:
        return {"reply": error}
    choice = dialect_mod.Choice(command.target, False, "named")
    return turns.done(new_env, scope, {"dialect": turns.dialect_state(choice)})
