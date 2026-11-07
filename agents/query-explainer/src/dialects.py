"""Pure: which dialect, and the parse. Order tried: the hinted dialect (words in the message), the dialect
sniffed from syntax (backticks, TOP, [brackets], `::`), then generic and the rest. The first dialect that
parses EVERY statement wins. sqlglot is used to parse only, never to execute."""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

ORDER = ("", "postgres", "mysql", "bigquery", "snowflake", "tsql", "sqlite", "redshift", "duckdb", "oracle")
LABELS = {"": "generic SQL", "postgres": "PostgreSQL", "mysql": "MySQL", "bigquery": "BigQuery",
          "snowflake": "Snowflake", "tsql": "T-SQL", "sqlite": "SQLite", "redshift": "Redshift", "duckdb": "DuckDB",
          "oracle": "Oracle"}
_HINTS = (
    ("postgres", r"postgres(?:ql)?|psql"), ("mysql", r"mysql|mariadb"), ("bigquery", r"big ?query|bq"),
    ("snowflake", r"snowflake"), ("tsql", r"t-?sql|sql server|mssql|azure sql"), ("sqlite", r"sqlite"),
    ("redshift", r"redshift"), ("duckdb", r"duck ?db"), ("oracle", r"oracle|pl/sql"),
)
_SNIFF = (
    ("tsql", re.compile(r"\bselect\s+(?:distinct\s+)?top\s*\(?\s*\d|\[\w+\]\.\[?\w|\bgetdate\s*\(|\bnolock\b", re.IGNORECASE)),
    ("bigquery", re.compile(r"`[\w-]+\.[\w-]+(?:\.[\w-]+)?`|\bsafe_divide\s*\(|\bunnest\s*\(", re.IGNORECASE)),
    ("mysql", re.compile(r"`\w+`|\bifnull\s*\(|\bdiv\b", re.IGNORECASE)),
    ("postgres", re.compile(r"::\s*\w|\bilike\b|\breturning\b|\binterval\s+'", re.IGNORECASE)),
    ("snowflake", re.compile(r"\bqualify\b|\biff\s*\(", re.IGNORECASE)),
)


@dataclass(frozen=True)
class Parsed:
    dialect: str                      # "" = generic
    statements: tuple[exp.Expression, ...]
    hinted: bool = False
    tried: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return LABELS.get(self.dialect, self.dialect)


def hinted(text: str) -> str | None:
    lowered = (text or "").casefold()
    for name, words in _HINTS:
        if re.search(rf"(?<![\w-])(?:{words})(?![\w-])", lowered):
            return name
    return None


def sniffed(sql: str) -> str | None:
    return next((name for name, rx in _SNIFF if rx.search(sql or "")), None)


def candidates(hint: str | None, sql: str) -> list[str]:
    first = [d for d in (hint, sniffed(sql)) if d is not None]
    return list(dict.fromkeys([*first, *ORDER]))


def parse(sql: str, hint: str | None = None) -> Parsed | None:
    """The first dialect that parses every statement, or None (the caller falls back)."""
    tried: list[str] = []
    for dialect in candidates(hint, sql):
        tried.append(dialect)
        statements = parse_with(sql, dialect)
        if statements:
            return Parsed(dialect, statements, hinted=dialect == hint, tried=tuple(tried))
    return None


def parse_with(sql: str, dialect: str) -> tuple[exp.Expression, ...]:
    try:
        trees = sqlglot.parse(sql, read=dialect or None, error_level=ErrorLevel.RAISE)
    except (SqlglotError, ValueError, RecursionError):
        return ()
    statements = tuple(t for t in trees if t is not None and not isinstance(t, exp.Semicolon))
    if not statements or any(isinstance(t, exp.Command) for t in statements):
        return ()  # a Command is sqlglot's "could not parse this, kept as text"
    return statements


def tried_labels(tried: tuple[str, ...] | list[str]) -> str:
    return ", ".join(LABELS.get(d, d) for d in tried)
