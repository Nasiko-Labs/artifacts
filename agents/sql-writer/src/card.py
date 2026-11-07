"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "sql-writer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Writes and optimizes SQL from what you paste into the chat. Write: paste your schema (CREATE TABLE "
    "statements, table(column type) lines or psql \\d output) and ask a question; it returns one read-only query "
    "in your dialect. Optimize: paste a slow query (optionally with EXPLAIN output and your indexes); it lists "
    "the problems found by rule (non-sargable filters, NOT IN with NULLs, SELECT *, deep OFFSET, fan-out before "
    "SUM, missing partition filters...), rewrites it, and gives a comparison query plus index suggestions. Every "
    "query it returns is parsed and checked in code with sqlglot: it must parse in the dialect, be read-only and "
    "use only tables and columns from your schema, or it is flagged \"not validated\". Dialects: PostgreSQL "
    "(default), MySQL, BigQuery, Snowflake, SQLite, Redshift, T-SQL, DuckDB, ClickHouse, Trino. Follow-ups: "
    "convert it to another dialect (exact, in code), sql only, pretty, one line, change or add a condition, as "
    "json. It never connects to a database or runs SQL, never writes UPDATE/DELETE/INSERT/DDL, and can't prove "
    "a rewrite returns the same rows. Not for explaining existing SQL in depth or schema design."
)

SKILL_ID = "write-optimize-sql"
SKILL_NAME = "Write or optimize SQL"
SKILL_DESCRIPTION = (
    "A validated read-only query from your schema and question, or a rewrite of a slow query with rule-based "
    "findings, a comparison query and index suggestions. SQL is parsed and checked with sqlglot, never run."
)
SKILL_TAGS = ["sql", "query", "optimize", "database", "postgres", "bigquery", "snowflake", "performance"]
SKILL_EXAMPLES = [
    "Monthly revenue by region from these tables: <paste CREATE TABLE statements>",
    "Optimize this query, it takes 40 s: <paste the query and EXPLAIN>",
    "Convert it to BigQuery",
]
