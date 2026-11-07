"""Pure: the envelope every reply carries: {summary, items, confidence, needs_human} plus this agent's fields
(`sql`, `explanation`, `assumptions`, mode, dialect, validated, checks, lint, suggested_indexes, sql_check, ...).
`items` mirrors `assumptions`. Confidence and needs_human are computed here from the checks."""

from __future__ import annotations

from dataclasses import dataclass, field

import dialect as dialect_mod
import result
from findings import Finding
from validate import Validation


@dataclass
class Draft:
    mode: str                                   # write | optimize | lint
    choice: dialect_mod.Choice
    sql: str
    validation: Validation | None
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    lint: list[Finding] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    suggested_indexes: list[dict] = field(default_factory=list)
    index_note: str = ""
    sql_check: str = ""
    sql_check_note: str = ""
    warnings: list[str] = field(default_factory=list)
    tables_considered: list[str] = field(default_factory=list)
    dropped: int = 0
    repairs: int = 0
    original_sql: str = ""
    no_rewrite_needed: bool = False
    partial_schema: bool = False


def _summary(d: Draft, validated: bool) -> str:
    if d.mode == "lint":
        return f"{len(d.lint)} findings in the pasted query (no rewrite requested)."
    if d.mode == "optimize":
        what = "No rewrite needed" if d.no_rewrite_needed else "Rewrite"
        state = "validated" if validated else f"not validated ({d.validation.failed if d.validation else 'no SQL'})"
        return f"{what} of a {d.choice.label} query, {state}; {len(d.lint)} findings."
    state = "validated" if validated else f"not validated ({d.validation.failed if d.validation else 'no SQL'})"
    return f"A read-only {d.choice.label} query, {state}."


def build(d: Draft) -> dict:
    validated = bool(d.validation and d.validation.ok) if d.mode != "lint" else True
    addressed = {str(c.get("finding_id")) for c in d.changes}
    unaddressed = tuple(f.id for f in d.lint if f.id not in addressed) if d.mode == "optimize" and \
        not d.no_rewrite_needed else ()
    fan_out = any(f.rule == "Q13" for f in d.lint) and d.mode != "lint"
    quality = result.Quality(validated, d.repairs, d.dropped, d.choice.assumed, tuple(d.warnings), unaddressed,
                             fan_out, d.partial_schema)
    checks = dict(d.validation.checks) if d.validation else {}
    return {
        "summary": _summary(d, validated),
        "items": list(d.assumptions),
        "confidence": result.confidence(quality),
        "needs_human": result.needs_human(quality),
        "mode": d.mode,
        "dialect": d.choice.name,
        "dialect_assumed": d.choice.assumed,
        "dialect_how": d.choice.how,
        "sql": d.sql,
        "explanation": d.explanation,
        "assumptions": list(d.assumptions),
        "validated": validated,
        "checks": checks,
        "failed_check": (d.validation.failed if d.validation else "") if not validated else "",
        "failed_reason": (d.validation.error if d.validation else "") if not validated else "",
        "lint": [f.to_dict() for f in d.lint],
        "changes": list(d.changes),
        "unaddressed": list(unaddressed),
        "suggested_indexes": list(d.suggested_indexes),
        "index_note": d.index_note,
        "sql_check": d.sql_check,
        "sql_check_note": d.sql_check_note,
        "warnings": list(d.warnings),
        "tables_considered": list(d.tables_considered),
        "dropped": d.dropped,
        "repairs": d.repairs,
        "original_sql": d.original_sql,
        "no_rewrite_needed": d.no_rewrite_needed,
    }
