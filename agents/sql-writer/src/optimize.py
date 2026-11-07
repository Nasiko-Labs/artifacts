"""Optimize mode: lint the pasted query and its EXPLAIN in code, one rewrite call with the findings, then the
same validation as write mode plus the rewrite checks (same output columns, same tables, ORDER BY + LIMIT kept);
at most two repairs. Then the comparison query and index suggestions are built in code. "Explain this" asks get
the findings only, with no call."""

from __future__ import annotations

import json
from dataclasses import dataclass

import envelope as envelope_mod
import equiv
import explain
import indexes
import prompts
import result
import validate
from dialect import Choice
from findings import numbered
from lint import lint
from llm import LlmError, TurnDeps, call_json
from schema import Schema

MAX_REPAIRS = 2
NOT_JSON = "the answer was not the JSON object asked for"
LINT_ONLY_NOTE = ("Explaining what a query does is the query explainer's job; here are its findings only. Say "
                  "\"optimize it\" for a rewrite.")


@dataclass(frozen=True)
class Job:
    request: str
    sql: str
    plan: str
    schema: Schema
    choice: Choice
    policy: tuple[str, ...] = ()
    lint_only: bool = False


@dataclass(frozen=True)
class Outcome:
    kind: str                 # ok | off_topic | model_failed | unparsed | write_statement
    envelope: dict | None = None
    error: str = ""


def findings_for(job: Job, expr):
    plan_found, plan_note = explain.read(job.plan)
    return numbered(lint(expr, job.choice.name, job.schema) + plan_found), plan_note


async def run(deps: TurnDeps, job: Job) -> Outcome:
    original = validate.validate(job.sql, job.choice.name, Schema())
    if original.failed == "parse":
        return Outcome("unparsed", error=original.error)
    if original.failed == "read_only":
        return Outcome("write_statement")
    found, plan_note = findings_for(job, original.expr)
    if job.lint_only:
        draft = envelope_mod.Draft("lint", job.choice, job.sql, original, LINT_ONLY_NOTE, [], found,
                                   warnings=[plan_note] if plan_note else [])
        draft.suggested_indexes, draft.index_note = indexes.suggest(found, job.schema, bool(job.plan.strip()))
        return Outcome("ok", envelope_mod.build(draft))
    base = prompts.optimize_messages(job.request, job.sql, [f.to_dict() for f in found], job.schema.digest(),
                                     job.choice.name, job.policy)
    try:
        data = await call_json(deps, base)
    except LlmError:
        return Outcome("model_failed")
    best, repairs = None, 0
    while True:
        if data is not None and data.get("status") == "off_topic":
            return Outcome("off_topic")
        checked = _check(job, data) if data is not None else None
        if checked is not None and (best is None or checked.rank > best[1].rank):
            best = (data, checked)
        if (checked is not None and checked.ok) or repairs >= MAX_REPAIRS or not deps.can_call():
            break
        repairs += 1
        try:
            data = await call_json(deps, prompts.repair_messages(base, data or {}, checked.error if checked else NOT_JSON))
        except LlmError:
            break
    if best is None:
        return Outcome("model_failed")
    return Outcome("ok", _finish(job, best[0], best[1], repairs, found, plan_note))


def _check(job: Job, data: dict) -> validate.Validation:
    sql = job.sql if data.get("no_rewrite_needed") is True else str(data.get("sql") or "")
    changes = json.dumps(data.get("changes") or [])
    return validate.validate(sql, job.choice.name, job.schema, original=job.sql, changes_text=changes)


def _finish(job: Job, data: dict, checked: validate.Validation, repairs: int, found, plan_note: str) -> dict:
    no_rewrite = data.get("no_rewrite_needed") is True
    sql = job.sql if no_rewrite else str(data.get("sql") or "").strip()
    grounding = result.Grounding(result.allowed_tokens(sql, job.sql, job.schema.digest(), job.plan, job.request))
    ids = {f.id for f in found}
    changes = []
    for change in data.get("changes") if isinstance(data.get("changes"), list) else []:
        what = grounding.text(str(change.get("what") or "")) if isinstance(change, dict) else ""
        if isinstance(change, dict) and str(change.get("finding_id")) in ids and what:
            changes.append({"finding_id": str(change["finding_id"]), "what": what})
        else:
            grounding.dropped += 1
    draft = envelope_mod.Draft(
        "optimize", job.choice, sql, checked, grounding.text(str(data.get("explanation") or "")),
        grounding.items(data.get("assumptions")), found, changes, warnings=list(checked.warnings),
        repairs=repairs, original_sql=job.sql, no_rewrite_needed=no_rewrite)
    draft.assumptions += result.mandatory(job.choice, bool(job.schema), not no_rewrite,
                                          checked.expr if checked.ok else None)
    draft.warnings += [plan_note] if plan_note else []
    draft.dropped = grounding.dropped
    if checked.ok and not no_rewrite:
        draft.sql_check, draft.sql_check_note = equiv.comparison(job.sql, sql, job.choice.name)
    draft.suggested_indexes, draft.index_note = indexes.suggest(found, job.schema, bool(job.plan.strip()))
    return envelope_mod.build(draft)
