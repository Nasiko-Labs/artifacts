"""Pure: every SQL this agent returns passes these checks in code (sqlglot), in this order:

    parse      exactly one statement in the dialect
    read_only  a SELECT / set operation (optionally under WITH); no write, DDL, SELECT INTO, FOR UPDATE, sleep
    schema     every table is in the pasted schema; `qualify` resolves every column (unknown or ambiguous fail)
    dialect    the SQL round-trips through the dialect; functions unknown to it are warnings
    projection (optimize only) same output columns in order, same base tables, ORDER BY + LIMIT kept

A failure names the check and carries sqlglot's message, which the repair call receives verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

import sqlshape
from schema import Schema

CHECK_ORDER = ("parse", "read_only", "schema", "dialect", "projection")
_WRITES = tuple(getattr(exp, n) for n in ("Insert", "Update", "Delete", "Merge", "Drop", "Alter", "Create",
                                          "TruncateTable", "Command", "Grant", "Revoke", "Copy", "Set")
                if hasattr(exp, n))
_READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)
_BANNED_FUNCS = ("pg_sleep", "sleep", "copy", "dblink", "benchmark", "waitfor", "xp_cmdshell", "load_file")


@dataclass(frozen=True)
class Validation:
    sql: str
    checks: dict = field(default_factory=dict)   # check -> True / False / None (not applicable)
    failed: str = ""
    error: str = ""
    warnings: tuple[str, ...] = ()
    expr: exp.Expression | None = None

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def rank(self) -> int:
        return sum(1 for c in CHECK_ORDER if self.checks.get(c) is not False)


def parse_one(sql: str, dialect: str) -> exp.Expression | None:
    try:
        trees = [t for t in sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.RAISE) if t is not None]
    except (SqlglotError, ValueError, RecursionError):
        return None
    return trees[0] if len(trees) == 1 and not isinstance(trees[0], exp.Command) else None


def validate(sql: str, dialect: str, schema: Schema, original: str | None = None,
             changes_text: str = "") -> Validation:
    checks: dict = {}

    def fail(check: str, error: str, expr=None) -> Validation:
        checks[check] = False
        return Validation(sql, dict(checks), check, error, (), expr)

    try:
        trees = [t for t in sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.RAISE) if t is not None]
    except (SqlglotError, ValueError, RecursionError) as e:
        return fail("parse", f"does not parse as {dialect}: {_first_line(e)}")
    if len(trees) != 1:
        return fail("parse", f"expected exactly one statement, found {len(trees)}")
    expr = trees[0]
    checks["parse"] = True
    problem = read_only_problem(expr)
    if problem:
        return fail("read_only", problem, expr)
    checks["read_only"] = True
    schema_problem = schema_fit(expr, dialect, schema) if schema else None
    checks["schema"] = None if not schema else not schema_problem
    if schema_problem:
        return fail("schema", schema_problem, expr)
    try:
        sqlglot.parse_one(expr.sql(dialect=dialect), read=dialect, error_level=ErrorLevel.RAISE)
    except (SqlglotError, ValueError) as e:
        return fail("dialect", f"does not round-trip in {dialect}: {_first_line(e)}", expr)
    checks["dialect"] = True
    warnings = tuple(f"`{name}()` isn't a known {dialect} function." for name in unknown_functions(expr))
    if original is not None:
        checks["projection"] = True
        problem = projection_problem(expr, original, dialect, schema, changes_text)
        if problem:
            checks["projection"] = False
            return Validation(sql, dict(checks), "projection", problem, warnings, expr)
    return Validation(sql, checks, "", "", warnings, expr)


def read_only_problem(expr: exp.Expression) -> str | None:
    root = expr.this if isinstance(expr, exp.Subquery) else expr
    if not isinstance(root, _READ_ROOTS):
        return f"only a read query (SELECT / WITH) is allowed, got {type(root).__name__.upper()}"
    for node in expr.walk():
        if isinstance(node, _WRITES):
            return f"contains a {type(node).__name__.upper()} statement; only reads are allowed"
        if isinstance(node, exp.Select) and node.args.get("into") is not None:
            return "SELECT ... INTO creates a table; only reads are allowed"
        if isinstance(node, exp.Select) and node.args.get("locks"):
            return "FOR UPDATE / FOR SHARE takes locks; only plain reads are allowed"
        if isinstance(node, exp.Func) and _banned(node):
            return f"calls `{_func_name(node)}()`, which is not allowed in a read query"
    return None


def _func_name(node: exp.Func) -> str:
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).casefold()


def _banned(node: exp.Func) -> bool:
    name = _func_name(node)
    return name in _BANNED_FUNCS or name.startswith(("lo_", "dblink"))


def unknown_functions(expr: exp.Expression) -> list[str]:
    return list(dict.fromkeys(n.name for n in expr.find_all(exp.Anonymous) if n.name))


def schema_fit(expr: exp.Expression, dialect: str, schema: Schema) -> str | None:
    ctes = sqlshape.cte_names(expr)
    for table in expr.find_all(exp.Table):
        if table.name and table.name.casefold() not in ctes and schema.table(table.name) is None:
            return f"table `{table.name}` is not in the schema"
    bare = expr.copy()
    for table in bare.find_all(exp.Table):
        table.set("db", None)
        table.set("catalog", None)
    try:
        qualify(bare, schema=MappingSchema(schema.mapping(), dialect=dialect), dialect=dialect,
                validate_qualify_columns=True, quote_identifiers=False)
    except (OptimizeError, SqlglotError, ValueError, KeyError) as e:
        return f"schema check failed: {_first_line(e)}"
    return None


def output_names(expr: exp.Expression, dialect: str, schema: Schema) -> list[str] | None:
    """Output column names in order (stars expanded through the schema), or None when a star stays unknown."""
    tree = expr
    if schema:
        try:
            tree = qualify(expr.copy(), schema=MappingSchema(schema.mapping(), dialect=dialect), dialect=dialect,
                           quote_identifiers=False)
        except (OptimizeError, SqlglotError, ValueError, KeyError):
            tree = expr
    names = [n.casefold() for n in getattr(tree, "named_selects", [])]
    return None if "*" in names or not names else names


def projection_problem(expr, original: str, dialect: str, schema: Schema, changes_text: str) -> str | None:
    orig = parse_one(original, dialect)
    if orig is None:
        return None
    before, after = output_names(orig, dialect, schema), output_names(expr, dialect, schema)
    if before is not None and after is not None and before != after:
        return f"output columns changed: the original returns {before}, the rewrite returns {after}"
    gone = sqlshape.base_tables(orig) ^ sqlshape.base_tables(expr)
    unexplained = sorted(t for t in gone if t not in changes_text.casefold())
    if unexplained:
        return f"base tables changed without a change entry naming them: {unexplained}"
    if sqlshape.order_and_limit(orig) and not sqlshape.order_and_limit(expr):
        return "the original has ORDER BY with LIMIT; the rewrite must keep both"
    return None


def _first_line(error: Exception) -> str:
    return " ".join(str(error).split("\n")[0].split())[:300]
