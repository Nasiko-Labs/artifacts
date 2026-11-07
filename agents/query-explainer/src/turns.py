"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

from dataclasses import replace

import analyze as analyze_mod
import dialects
import envelope as envelope_mod
import explain
import intake as intake_mod
import prompts
import render
import schema
import smalltalk
from findings import Finding
from inputs import Incoming
from llm import LlmError, TurnDeps, complete_json
from placeholders import Masked


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("envelope")))
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


def schema_text(parsed: dialects.Parsed | None, hints, extra_ddl=()) -> str:
    ddl = [s.sql(dialect=parsed.dialect or None) for s in parsed.statements if schema.is_ddl(s)] \
        if parsed else []
    return "\n".join([*ddl, *extra_ddl, *hints])


def coverage(analysis: analyze_mod.Analysis, results: list[explain.Explained], masked: Masked) -> list[str]:
    notes = []
    if not analysis.hinted:
        notes.append(f"Dialect auto-detected as {analysis.label}; name your database (for example \"this is "
                     "BigQuery\") if that's wrong.")
    if masked.originals:
        notes.append(f"Templated SQL: {len(masked.originals)} placeholders (template expressions or bind "
                     "parameters) were parsed as stand-in names.")
    if masked.removed_tags:
        notes.append(f"{masked.removed_tags} template control tags were removed for parsing.")
    by_reason: dict[str, list[str]] = {}
    for r in results:
        if r.code_only:
            by_reason.setdefault(r.code_only, []).append(str(r.index))
    for reason, idx in by_reason.items():
        notes.append(f"Statement{'s' if len(idx) > 1 else ''} {', '.join(idx)}: summarised by code only ({reason}); "
                     "the rule findings still apply.")
    if analysis.skipped:
        notes.append(f"{analysis.skipped} statements after the 10th were not analysed.")
    return notes


def result_state(analysis, results, masked, scope: Scope, notes) -> dict:
    env = envelope_mod.build(analysis, results, masked, tuple(notes))
    allowed = scope.incoming.text
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, allowed)
    return {"envelope": env, "data": env, "reply": reply}


def stored_results(results: list[explain.Explained]) -> list[dict]:
    return [{"lines": list(r.lines), "notes": {f.cid: {"so_what": f.so_what, "hint": f.hint} for f in r.findings},
             "extras": [f.to_dict() for f in r.extras], "narrative_ok": r.narrative_ok, "repaired": r.repaired,
             "code_only": r.code_only} for r in results]


def restore(analysis: analyze_mod.Analysis, saved: list[dict]) -> list[explain.Explained]:
    out = []
    for s in analysis.statements:
        old = saved[s.index - 1] if s.index - 1 < len(saved) else {"code_only": "not explained", "notes": {}}
        notes = old.get("notes") or {}
        findings = tuple(replace(f, so_what=notes.get(f.cid, {}).get("so_what", ""),
                                 hint=notes.get(f.cid, {}).get("hint", "")) for f in s.findings)
        out.append(explain.Explained(s.index, tuple(old.get("lines") or ()), findings,
                                     tuple(Finding.from_dict(e) for e in old.get("extras") or ()),
                                     narrative_ok=bool(old.get("narrative_ok", True)),
                                     repaired=bool(old.get("repaired")), code_only=old.get("code_only") or ""))
    return out


async def run(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    parsed, policy = route.parsed, scope.incoming.policy
    if parsed.parsed is None:
        return await _fallback(parsed, route, scope)
    analysis = analyze_mod.analyze(parsed.parsed, route.hints)
    text = schema_text(parsed.parsed, route.hints)
    results = await explain.explain_all(scope.deps, analysis, text, parsed.request, policy)
    if any(r.off_topic for r in results) and not any(r.lines for r in results):
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if not any(r.lines for r in results) and all(r.code_only == "model failed" for r in results[:explain.MAX_EXPLAINED]):
        return {"reply": prompts.MODEL_FAILED}
    notes = [*route.notes, *coverage(analysis, results, parsed.masked)]
    stored = {"masked_sql": parsed.masked.sql, "originals": dict(parsed.masked.originals),
              "removed_tags": parsed.masked.removed_tags, "dialect": parsed.parsed.dialect,
              "hinted": parsed.parsed.hinted, "hints": list(route.hints), "extra_ddl": [],
              "request": parsed.request, "results": stored_results(results)}
    return {**stored, **result_state(analysis, results, parsed.masked, scope, notes)}


async def _fallback(parsed, route, scope: Scope) -> dict:
    tried = dialects.tried_labels(dialects.candidates(None, parsed.masked.sql))
    result = await explain.fallback(scope.deps, parsed.masked.sql, "\n".join(route.hints), parsed.request,
                                    scope.incoming.policy)
    if result.status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if result.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    notes = (*route.notes, prompts.PARSE_FAIL_NOTE.format(tried=tried))
    env = envelope_mod.build_fallback(result, tried, parsed.masked, notes)
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, scope.incoming.text)
    return {"envelope": env, "data": env, "reply": reply, "results": [], "masked_sql": ""}


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    has_result = bool(state.get("envelope"))

    def valid(data):
        return data if isinstance(data, dict) and data.get("status") in ("off_topic", "needs_sql", "followup") \
            else None

    try:
        data = await complete_json(scope.deps, prompts.scope_messages(scope.incoming.text, has_result,
                                                                      scope.incoming.policy),
                                   prompts.SCOPE_CONTRACT, valid, max_tokens=50)
    except LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if data["status"] == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if has_result:
        return {"reply": prompts.FOLLOWUP_HELP}
    return {"pause": intake_mod.need_sql(scope.incoming, scope.incoming.text).to_metadata()}
