"""Follow-ups on the stored result that need no model call: sql only, explain, pretty, one line, as json, and
"convert it to <dialect>" (exact, `sqlglot.transpile`, then validated again in the target dialect)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot.errors import ErrorLevel, SqlglotError, UnsupportedError

import dialect as dialect_mod
import envelope as envelope_mod
import validate
from findings import Finding
from lint import lint
from schema import Schema

RISK = ("Date, string and NULL-ordering functions differ between dialects; run it on a sample before relying on "
        "it.")
_SQL_ONLY = re.compile(r"^(?:just |only |show )?(?:me )?(?:the )?(?:sql|query)(?: only| please)?$|^sql only$")
_EXPLAIN = re.compile(r"^(?:explain(?: it| that| this| the query)?|why|what does it do)\??$")
_PRETTY = re.compile(r"^(?:pretty(?: print)?(?: it)?|format(?: it)?|prettify(?: it)?)$")
_ONE_LINE = re.compile(r"^(?:(?:on |as |in )?(?:a )?(?:one|single) line|one-liner|minify(?: it)?)$")
_JSON = re.compile(r"^(?:as json|json only|in json|json)$")
_TRANSPILE = re.compile(r"^(?:please )?(?:convert|translate|transpile|port|rewrite|change|make|turn)?\s*(?:it|this|"
                        r"that|the query|the sql)?\s*(?:to|into|for|in)\s+(.{2,40})$")
_ROWS = re.compile(r"\b(?:show|list|see|which|what)\b.*\brows?\b|\brows? (?:it|that) would\b")


@dataclass(frozen=True)
class Command:
    kind: str                 # sql_only | explain | pretty | one_line | json | transpile | rows
    target: str = ""


def parse(text: str, has_pending_write: bool) -> Command | None:
    plain = " ".join(re.sub(r"[.!]+$", "", (text or "").casefold()).split())
    for kind, rx in (("sql_only", _SQL_ONLY), ("explain", _EXPLAIN), ("pretty", _PRETTY),
                     ("one_line", _ONE_LINE), ("json", _JSON)):
        if rx.match(plain):
            return Command(kind)
    move = _TRANSPILE.match(plain)
    if move and (target := dialect_mod.named(move.group(1))):
        return Command("transpile", target)
    if has_pending_write and _ROWS.search(plain):
        return Command("rows")
    return None


def reformat(env: dict, pretty: bool) -> str:
    expr = validate.parse_one(env["sql"], env["dialect"])
    if expr is None:
        return env["sql"]
    return expr.sql(dialect=env["dialect"], pretty=pretty)


def transpile_sql(sql: str, source: str, target: str) -> tuple[str, list[str]]:
    """(sql in target, risk notes); ("", [reason]) when sqlglot can't express it."""
    if not sql:
        return "", []
    try:
        return sqlglot.transpile(sql, read=source, write=target, pretty=True,
                                 unsupported_level=ErrorLevel.RAISE)[0], []
    except UnsupportedError as e:
        try:
            out = sqlglot.transpile(sql, read=source, write=target, pretty=True)[0]
        except (SqlglotError, ValueError):
            return "", [f"sqlglot can't express this in {target}: {e}"]
        detail = " ".join(str(e).split())[:200]
        return out, [f"Part of the query has no exact {dialect_mod.LABELS[target]} equivalent: {detail}"]
    except (SqlglotError, ValueError) as e:
        return "", [f"sqlglot can't express this in {target}: {' '.join(str(e).split())[:200]}"]


def transpile(env: dict, target: str, schema: Schema) -> tuple[dict | None, str]:
    """(new envelope, error). The result is validated again in the target dialect; 0 model calls."""
    source = env["dialect"]
    sql, risks = transpile_sql(env["sql"], source, target)
    if not sql:
        return None, risks[0] if risks else "There's no query to convert."
    checked = validate.validate(sql, target, schema)
    choice = dialect_mod.Choice(target, False, "named")
    kept = [a for a in env["assumptions"] if not a.startswith("Dialect assumed")]
    note = f"Converted from {dialect_mod.LABELS[source]} in code (sqlglot), then checked again; not run anywhere."
    found: list[Finding] = lint(checked.expr, target, schema) if checked.ok else []
    draft = envelope_mod.Draft(env["mode"] if env["mode"] != "lint" else "write", choice, sql, checked,
                               env.get("explanation", ""), [*kept, note], found,
                               warnings=[*checked.warnings, *risks, RISK], dropped=env.get("dropped", 0))
    if env.get("original_sql"):
        draft.original_sql = transpile_sql(env["original_sql"], source, target)[0]
        draft.sql_check = transpile_sql(env.get("sql_check", ""), source, target)[0]
        draft.sql_check_note = env.get("sql_check_note", "")
        draft.changes = list(env.get("changes") or [])
        draft.lint = [Finding.from_dict(f) for f in env.get("lint") or []]
        draft.suggested_indexes, draft.index_note = env.get("suggested_indexes") or [], env.get("index_note", "")
    return envelope_mod.build(draft), ""
