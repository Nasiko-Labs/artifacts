"""Write mode: one generate call, then validation in code; on failure up to two repair calls carrying the
validator's error. After that the best attempt is shown flagged "not validated", never passed off as correct.
The model may instead ask for a column or a join path; the options are then computed from the schema."""

from __future__ import annotations

import re
from dataclasses import dataclass

import candidates
import envelope as envelope_mod
import prompts
import prune
import result
import validate
from dialect import Choice
from findings import Finding
from lint import lint
from llm import LlmError, TurnDeps, call_json
from schema import Schema

MAX_REPAIRS = 2
NOT_JSON = "the answer was not the JSON object asked for"
_TOP_N = re.compile(r"\btop\s+(\d{1,4})\b", re.IGNORECASE)


@dataclass(frozen=True)
class Ask:
    question: str
    schema: Schema
    choice: Choice
    policy: tuple[str, ...] = ()
    previous_sql: str = ""
    allowed_text: str = ""


@dataclass(frozen=True)
class Outcome:
    kind: str                 # ok | off_topic | needs_column | ambiguous_join | model_failed
    envelope: dict | None = None
    options: tuple[str, ...] = ()
    concept: str = ""
    tables: tuple[str, ...] = ()


async def write(deps: TurnDeps, ask: Ask) -> Outcome:
    seen, considered = prune.prune(ask.schema, ask.question)
    base = prompts.write_messages(ask.question, seen.digest(), ask.choice.name, ask.policy, ask.previous_sql)
    try:
        data = await call_json(deps, base)
    except LlmError:
        return Outcome("model_failed")
    best: tuple[dict, validate.Validation] | None = None
    repairs = 0
    while True:
        status = str((data or {}).get("status") or "ok")
        if data is not None and status == "off_topic":
            return Outcome("off_topic")
        if data is not None and status in ("needs_column", "ambiguous_join"):
            return _question(data, ask.schema)
        checked = validate.validate(str(data.get("sql") or ""), ask.choice.name, ask.schema) if data else None
        if checked is not None and (best is None or checked.rank > best[1].rank):
            best = (data, checked)
        if (checked is not None and checked.ok) or repairs >= MAX_REPAIRS or not deps.can_call():
            break
        repairs += 1
        error = checked.error if checked is not None else NOT_JSON
        try:
            data = await call_json(deps, prompts.repair_messages(base, data or {}, error))
        except LlmError:
            break
    if best is None:
        return Outcome("model_failed")
    return Outcome("ok", finish(ask, best[0], best[1], repairs, considered))


def finish(ask: Ask, data: dict, checked: validate.Validation, repairs: int, considered: list[str]) -> dict:
    sql, checked, notes = _top_n(ask, str(data.get("sql") or "").strip(), checked)
    grounding = result.Grounding(result.allowed_tokens(sql, ask.schema.digest(), ask.question, ask.previous_sql,
                                                       ask.allowed_text))
    explanation = grounding.text(str(data.get("explanation") or ""))
    assumptions = grounding.items(data.get("assumptions")) + notes
    tables = grounding.names(data.get("tables"), {t.name.casefold() for t in ask.schema.tables})
    grounding.names(data.get("columns"), {c.name.casefold() for t in ask.schema.tables for c in t.columns})
    findings: list[Finding] = lint(checked.expr, ask.choice.name, ask.schema) if checked.ok else []
    draft = envelope_mod.Draft(
        "write", ask.choice, sql, checked, explanation,
        assumptions + result.mandatory(ask.choice, bool(ask.schema), False, checked.expr if checked.ok else None),
        findings, warnings=list(checked.warnings), tables_considered=considered, dropped=grounding.dropped,
        repairs=repairs, partial_schema=len(tables) < len(data.get("tables") or []))
    return envelope_mod.build(draft)


def _top_n(ask: Ask, sql: str, checked: validate.Validation) -> tuple[str, validate.Validation, list[str]]:
    """A "top N" question gets a LIMIT N when the validated query has none (and the reply says so)."""
    match = _TOP_N.search(ask.question)
    if not match or not checked.ok or checked.expr.args.get("limit") is not None:
        return sql, checked, []
    limited = checked.expr.copy().limit(int(match.group(1))).sql(dialect=ask.choice.name, pretty=True)
    again = validate.validate(limited, ask.choice.name, ask.schema)
    if not again.ok:
        return sql, checked, []
    return limited, again, [f"Added LIMIT {match.group(1)} because you asked for the top {match.group(1)}."]


def _question(data: dict, schema: Schema) -> Outcome:
    tables = tuple(str(t) for t in data.get("join_tables") or data.get("tables") or [] if isinstance(t, str))
    if data.get("status") == "needs_column":
        concept = str(data.get("concept") or "").strip()
        return Outcome("needs_column", options=tuple(candidates.columns(schema, concept, list(tables))),
                       concept=concept, tables=tables)
    pair = tables[:2]
    options = tuple(candidates.joins(schema, *pair)) if len(pair) == 2 else ()
    return Outcome("ambiguous_join", options=options, tables=pair)
