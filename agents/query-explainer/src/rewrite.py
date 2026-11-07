"""The "rewrite the join as ..." follow-up: one model call; the suggested SQL must parse in the stored dialect
and use only tables and columns from the original SQL or the pasted schema (new aliases are allowed). It is
always shown as an untested suggestion."""

from __future__ import annotations

from sqlglot import exp

import dialects
import prompts
from analyze import Analysis
from llm import LlmError, TurnDeps, complete_json

CANNOT = ("I couldn't produce a rewrite that parses and uses only the tables and columns in your SQL, so I'm not "
          "showing one.")


def _valid(data) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in ("ok", "off_topic", "cannot"):
        return None
    return data


def defined_aliases(tree: exp.Expression) -> set[str]:
    names = {a.alias.casefold() for a in tree.find_all(exp.Alias) if a.alias}
    names |= {t.alias_or_name.casefold() for t in tree.find_all(exp.Table)}
    names |= {s.alias.casefold() for s in tree.find_all(exp.Subquery) if s.alias}
    return names | {c.alias.casefold() for c in tree.find_all(exp.CTE)}


def unknown_in(sql: str, dialect: str, analysis: Analysis) -> list[str] | None:
    """Names the rewrite uses that the original does not have; None when it does not parse."""
    trees = dialects.parse_with(sql, dialect)
    if not trees:
        return None
    bad: list[str] = []
    for tree in trees:
        local = defined_aliases(tree)
        for table in tree.find_all(exp.Table):
            if table.name.casefold() not in analysis.names and table.name.casefold() not in local:
                bad.append(table.name)
        for column in tree.find_all(exp.Column):
            name = column.name.casefold()
            if name and name not in analysis.names and name not in local:
                bad.append(column.name)
    return bad


async def suggest(deps: TurnDeps, analysis: Analysis, ask: str, schema_text: str,
                  policy: tuple[str, ...]) -> tuple[str, str, str]:
    """(status, sql, explanation); status is ok | off_topic | cannot | failed."""
    pretty = "\n;\n".join(s.pretty for s in analysis.statements)
    try:
        data = await complete_json(deps, prompts.rewrite_messages(pretty, ask, schema_text, policy),
                                   prompts.REWRITE_CONTRACT, _valid)
    except LlmError:
        return "failed", "", ""
    if data["status"] != "ok":
        return data["status"], "", ""
    sql = str(data.get("sql") or "").strip().strip("`")
    unknown = unknown_in(sql, analysis.dialect, analysis) if sql else None
    if unknown is None or unknown:
        return "cannot", "", ""
    return "ok", sql, " ".join(str(data.get("explanation") or "").split())[:600]
