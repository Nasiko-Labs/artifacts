"""Pure: the comparison query that lets the user check a rewrite returns the same rows. Equivalence can't be
proven without data, so the agent hands over a query instead: rows only in the original, then rows only in the
rewrite (EXCEPT both ways, joined with UNION ALL). Built and parse-checked with sqlglot in the user's dialect."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

NOTE = ("Run it on a sample or a limited time window. An empty result means the same set of rows (duplicates and "
        "row order are ignored).")
HASH_NOTE = "For PostgreSQL, the second query also compares row counts and a hash of the ordered rows."
NO_EXCEPT = {"clickhouse"}  # EXCEPT semantics differ enough that a check could mislead


def _strip(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def comparison(original: str, rewrite: str, dialect: str) -> tuple[str, str]:
    """(sql_check, note); ("", reason) when the dialect can't express it."""
    if dialect in NO_EXCEPT:
        return "", f"No comparison query for {dialect}: its EXCEPT differs; compare counts by hand."
    o, r = _strip(original), _strip(rewrite)
    try:
        left, right = (sqlglot.parse_one(q, read=dialect, error_level=ErrorLevel.RAISE) for q in (o, r))
        tree = exp.union(_side("only_in_original", left, "o", right, "r"),
                         _side("only_in_rewrite", right, "r", left, "o"), distinct=False)
        built = tree.sql(dialect=dialect, pretty=True)
        sqlglot.parse_one(built, read=dialect, error_level=ErrorLevel.RAISE)
    except (SqlglotError, ValueError):
        return "", "I couldn't build a comparison query in this dialect; compare row counts by hand."
    note = NOTE
    if dialect == "mysql":
        note += " MySQL needs 8.0.31 or later for EXCEPT."
    if dialect == "postgres":
        hashed = _postgres_hash(o, r)
        if hashed:
            return built + ";\n\n" + hashed, note + " " + HASH_NOTE
    return built, note


def _side(label: str, first, first_alias: str, second, second_alias: str) -> exp.Select:
    """SELECT '<label>' AS side, d.* FROM (SELECT * FROM (first) EXCEPT SELECT * FROM (second)) AS d"""
    diff = exp.except_(exp.select("*").from_(first.copy().subquery(first_alias)),
                       exp.select("*").from_(second.copy().subquery(second_alias)), distinct=True)
    return exp.select(exp.Literal.string(label).as_("side"), "d.*").from_(diff.subquery("d"))


def _postgres_hash(o: str, r: str) -> str:
    text = (f"SELECT (SELECT COUNT(*) FROM ({o}) AS o) AS original_rows, (SELECT COUNT(*) FROM ({r}) AS r) AS "
            f"rewrite_rows, (SELECT md5(string_agg(o::text, '|' ORDER BY o::text)) FROM ({o}) AS o) AS original_hash, "
            f"(SELECT md5(string_agg(r::text, '|' ORDER BY r::text)) FROM ({r}) AS r) AS rewrite_hash")
    try:
        return sqlglot.parse_one(text, read="postgres", error_level=ErrorLevel.RAISE).sql(dialect="postgres",
                                                                                          pretty=True)
    except (SqlglotError, ValueError):
        return ""
