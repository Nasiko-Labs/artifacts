"""Follow-up turns on the SQL stored in this conversation: re-render as JSON, re-check against new schema facts
(code only, zero calls), explain one CTE (one call), or suggest an untested rewrite (one call)."""

from __future__ import annotations

import analyze as analyze_mod
import dialects
import explain
import prompts
import render
import rewrite
import schema
import smalltalk
import sqltext
from placeholders import Masked, mask, unmask
from turns import Scope, coverage, restore, result_state, schema_text, stored_results

NO_SQL_STORED = "There's no parsed SQL in this conversation to work on. Paste the query first."
SCHEMA_NOTE = "Re-checked against your new schema facts in code; the narrative is unchanged."


def _stored(state: dict) -> tuple[dialects.Parsed | None, Masked]:
    masked = Masked(state.get("masked_sql") or "", dict(state.get("originals") or {}), state.get("removed_tags") or 0)
    dialect = state.get("dialect") or ""
    statements = dialects.parse_with(masked.sql, dialect) if masked.sql else ()
    if not statements:
        return None, masked
    return dialects.Parsed(dialect, statements, hinted=bool(state.get("hinted"))), masked


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    env = state.get("envelope") or {}
    if command.kind == "json":
        return {"reply": render.render_json(env), "data": env}
    parsed, masked = _stored(state)
    if parsed is None:
        return {"reply": NO_SQL_STORED}
    if command.kind == "schema":
        return _schema(state, scope, parsed, masked, command.text)
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    if command.kind == "cte":
        return await _cte(state, scope, parsed, masked, command.name)
    return await _rewrite(state, scope, parsed, masked, command.text)


def _schema(state: dict, scope: Scope, parsed, masked: Masked, text: str) -> dict:
    extracted = sqltext.extract(text)
    new_ddl = [s for s in dialects.parse_with(mask(extracted.sql).sql, parsed.dialect) if schema.is_ddl(s)] \
        if extracted.sql else []
    hints = [*(state.get("hints") or []), *extracted.hints]
    extra = [*(state.get("extra_ddl") or []), *(s.sql(dialect=parsed.dialect or None) for s in new_ddl)]
    old_ddl = [s for e in state.get("extra_ddl") or [] for s in dialects.parse_with(e, parsed.dialect)]
    analysis = analyze_mod.analyze(parsed, hints, [*old_ddl, *new_ddl])
    results = restore(analysis, state.get("results") or [])
    notes = [SCHEMA_NOTE, *coverage(analysis, results, masked)]
    return {"hints": hints, "extra_ddl": extra, "results": stored_results(results),
            **result_state(analysis, results, masked, scope, notes)}


async def _cte(state: dict, scope: Scope, parsed, masked: Masked, name: str) -> dict:
    query = analyze_mod.focus_cte(parsed, name)
    if query is None:
        names = ", ".join(f"`{n}`" for n in analyze_mod.cte_names(parsed)) or "none"
        return {"reply": f"There's no CTE called `{name}` in the stored SQL. CTEs: {names}."}
    ddl = [s for s in parsed.statements if schema.is_ddl(s)]
    extra = [s for e in state.get("extra_ddl") or [] for s in dialects.parse_with(e, parsed.dialect)]
    focused = dialects.Parsed(parsed.dialect, (query, *ddl), hinted=parsed.hinted)
    analysis = analyze_mod.analyze(focused, state.get("hints") or [], extra)
    results = await explain.explain_all(scope.deps, analysis, schema_text(focused, state.get("hints") or [],
                                                                          state.get("extra_ddl") or []),
                                        f"Explain only the CTE {name}.", scope.incoming.policy)
    if not any(r.lines for r in results):
        return {"reply": prompts.MODEL_FAILED}
    notes = [f"Only the CTE `{name}`; the stored explanation of the full query is unchanged."]
    view = result_state(analysis, results, masked, scope, notes)
    return {"reply": view["reply"], "data": view["data"]}


async def _rewrite(state: dict, scope: Scope, parsed, masked: Masked, ask: str) -> dict:
    hints, extra = state.get("hints") or [], state.get("extra_ddl") or []
    extra_trees = [s for e in extra for s in dialects.parse_with(e, parsed.dialect)]
    analysis = analyze_mod.analyze(parsed, hints, extra_trees)
    status, sql, explanation = await rewrite.suggest(scope.deps, analysis, ask, schema_text(parsed, hints, extra),
                                                     scope.incoming.policy)
    if status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if status != "ok":
        return {"reply": rewrite.CANNOT}
    shown = unmask(sql, masked.originals)
    return {"reply": render.render_rewrite(shown, unmask(explanation, masked.originals), scope.incoming.text),
            "data": {"summary": "Untested rewrite suggestion.", "items": [{"kind": "rewrite", "sql": shown,
                                                                         "untested": True}],
                     "rewrite": shown, "explanation": explanation, "confidence": "low", "needs_human": True}}
