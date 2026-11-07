"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "query-explainer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Explains SQL you paste into the chat: what the query computes, step by step in plain English, the "
    "assumptions it silently makes (join matches, key uniqueness, NULL handling, time zones, tie-breaking) and "
    "its performance and correctness smells (a LEFT JOIN that became an inner join, NOT IN with NULLs, "
    "fan-out before SUM, functions on filtered columns, UPDATE or DELETE without WHERE). The SQL is parsed in "
    "code with sqlglot (PostgreSQL, MySQL, BigQuery, Snowflake, T-SQL, SQLite, Redshift, DuckDB, Oracle), so "
    "every finding is a rule tied to a fragment of your SQL; paste CREATE TABLE / CREATE INDEX statements and "
    "it confirms uniqueness and index questions from them. It never runs SQL, has no database access and never "
    "claims a query is slow. Follow-ups: explain only one CTE, assume a column is NOT NULL, suggest an untested "
    "rewrite, as json. Not for writing new queries, schema design or database administration."
)

SKILL_ID = "explain-sql"
SKILL_NAME = "Explain a SQL query"
SKILL_DESCRIPTION = (
    "Plain-English steps of what pasted SQL computes, its hidden assumptions and its smells, each tied to a "
    "fragment of the SQL; pasted table definitions confirm keys and indexes. Read-only: SQL is parsed, never run."
)
SKILL_TAGS = ["sql", "query", "explain", "code review", "database", "performance", "postgres", "bigquery"]
SKILL_EXAMPLES = [
    "Explain this query: <paste SQL>",
    "What does this UPDATE do? <paste the UPDATE and the CREATE TABLE>",
    "Any problems with this BigQuery SQL? <paste SQL>",
]
