"""Pure: which SQL dialect. An explicit name in the message wins ("dialect: bigquery", "in Snowflake"); else
syntax is sniffed from the pasted SQL and confirmed by a sqlglot parse; else postgres, and the reply says so."""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot.errors import ErrorLevel, SqlglotError

DEFAULT = "postgres"
DIALECTS = ("postgres", "mysql", "bigquery", "snowflake", "sqlite", "redshift", "tsql", "duckdb", "clickhouse",
            "trino")
LABELS = {"postgres": "PostgreSQL", "mysql": "MySQL", "bigquery": "BigQuery", "snowflake": "Snowflake",
          "sqlite": "SQLite", "redshift": "Redshift", "tsql": "T-SQL", "duckdb": "DuckDB",
          "clickhouse": "ClickHouse", "trino": "Trino"}
_WORDS = (
    ("postgres", r"postgres(?:ql)?|psql|pg"), ("mysql", r"mysql|mariadb"), ("bigquery", r"big ?query|bq"),
    ("snowflake", r"snowflake"), ("sqlite", r"sqlite3?"), ("redshift", r"redshift"),
    ("tsql", r"t-?sql|sql server|mssql|azure sql"), ("duckdb", r"duck ?db"), ("clickhouse", r"click ?house"),
    ("trino", r"trino|presto|athena"),
)
_EXPLICIT = re.compile(r"\bdialect\s*[:=]\s*([\w -]+)", re.IGNORECASE)
_SNIFF = (
    ("tsql", re.compile(r"\bselect\s+(?:distinct\s+)?top\s*\(?\s*\d|\[\w+\]", re.IGNORECASE)),
    ("bigquery", re.compile(r"`[\w-]+\.[\w-]+\.[\w-]+`", re.IGNORECASE)),
    ("mysql", re.compile(r"`\w+`")),
    ("snowflake", re.compile(r"\bqualify\b|\bflatten\s*\(", re.IGNORECASE)),
    ("postgres", re.compile(r"::\s*\w|\bilike\b", re.IGNORECASE)),
)


@dataclass(frozen=True)
class Choice:
    name: str
    assumed: bool        # True only when nothing named or implied a dialect
    how: str             # named | detected | assumed

    @property
    def label(self) -> str:
        return LABELS[self.name]


def named(text: str) -> str | None:
    """A dialect the user names in words, or None."""
    lowered = (text or "").casefold()
    explicit = _EXPLICIT.search(lowered)
    scope = explicit.group(1) if explicit else lowered
    for name, words in _WORDS:
        if re.search(rf"(?<![\w-])(?:{words})(?![\w-])", scope):
            return name
    return None


def sniffed(sql: str) -> str | None:
    """A dialect implied by syntax, kept only when sqlglot parses the SQL in it."""
    for name, rx in _SNIFF:
        if rx.search(sql or "") and parses(sql, name):
            return name
    return None


def parses(sql: str, dialect: str) -> bool:
    try:
        return bool([t for t in sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.RAISE) if t is not None])
    except (SqlglotError, ValueError, RecursionError):
        return False


def choose(request: str, sql_texts: tuple[str, ...] = ()) -> Choice:
    name = named(request)
    if name:
        return Choice(name, False, "named")
    for text in sql_texts:
        found = sniffed(text)
        if found:
            return Choice(found, False, "detected")
    return Choice(DEFAULT, True, "assumed")


def describe(choice: Choice) -> str:
    suffix = {"named": "", "detected": " (detected)", "assumed": " (assumed)"}[choice.how]
    return f"{choice.name}{suffix}"
