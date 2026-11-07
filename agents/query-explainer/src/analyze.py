"""Pure: everything code knows about the pasted SQL before any model call: the schema from DDL and hint lines,
and per query statement the parse summary, the numbered smells and assumption candidates, the names the
narrative must mention, and the names it may use."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlglot import exp

import assumptions
import checks
import schema as schema_mod
import smells
import structure
from dialects import Parsed
from findings import Ctx, Finding, number

MAX_STATEMENTS = 10
SKIPPED_TYPES = (exp.Transaction, exp.Commit, exp.Rollback)


@dataclass(frozen=True)
class Statement:
    index: int                      # 1-based among query statements
    kind: str
    pretty: str
    flat: str
    summary: dict
    findings: tuple[Finding, ...]
    must_mention: tuple[str, ...]


@dataclass(frozen=True)
class Analysis:
    dialect: str
    label: str
    hinted: bool
    statements: tuple[Statement, ...]
    schema: schema_mod.Schema
    names: frozenset[str]
    corpus: str                     # every statement, flat and pretty, for evidence checks
    skipped: int = 0                # query statements over MAX_STATEMENTS, not analysed
    ddl_only: bool = False


def analyze(parsed: Parsed, hint_lines: Iterable[str] = (), extra_ddl: Iterable[exp.Expression] = ()) -> Analysis:
    all_statements = tuple(parsed.statements)
    ddl = [s for s in all_statements if schema_mod.is_ddl(s)] + list(extra_ddl)
    queries = [s for s in all_statements if not schema_mod.is_ddl(s) and not isinstance(s, SKIPPED_TYPES)]
    sch = schema_mod.build(ddl, hint_lines)
    built = tuple(_statement(i, s, parsed.dialect, sch, all_statements)
                  for i, s in enumerate(queries[:MAX_STATEMENTS], start=1))
    corpus = "\n".join([*(s.flat for s in built), *(s.pretty for s in built),
                        *(s.sql(dialect=parsed.dialect or None) for s in ddl)])
    names = checks.known_names([*all_statements, *extra_ddl]) | frozenset(sch.names())
    return Analysis(parsed.dialect, parsed.label, parsed.hinted, built, sch, names, corpus,
                    skipped=max(0, len(queries) - MAX_STATEMENTS), ddl_only=bool(ddl) and not queries)


def _statement(index: int, node: exp.Expression, dialect: str, sch: schema_mod.Schema,
               all_statements: tuple[exp.Expression, ...]) -> Statement:
    ctx = Ctx(node, dialect, sch, all_statements)
    findings = number(smells.detect(ctx)) + number(assumptions.detect(ctx))
    read = dialect or None
    return Statement(index, structure.kind_of(node), node.sql(dialect=read, pretty=True), node.sql(dialect=read),
                     structure.summarize(node, dialect), tuple(findings), tuple(structure.names_to_mention(node)))


def focus_cte(parsed: Parsed, name: str) -> exp.Expression | None:
    """The query of the CTE called `name` (with the WITH clause it depends on), as its own statement."""
    for statement in parsed.statements:
        for cte in statement.find_all(exp.CTE):
            if cte.alias.casefold() == name.casefold():
                query = cte.this.copy()
                with_ = statement.args.get("with_")
                if with_ is not None:
                    earlier = [c.copy() for c in with_.expressions if c is not cte][:with_.expressions.index(cte)]
                    if earlier:
                        query.set("with_", exp.With(expressions=earlier))
                return query
    return None


def cte_names(parsed: Parsed) -> list[str]:
    return [cte.alias for s in parsed.statements for cte in s.find_all(exp.CTE)]
