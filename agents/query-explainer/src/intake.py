"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                         -> canned intro (no call)
    SQL with a query statement                    -> run (parse in code, then explain)
    SQL that parses in no dialect                 -> run (fallback reading, confidence low)
    only DDL / schema lines, a result stored      -> follow-up: re-check the stored SQL against them
    only DDL / schema lines, nothing stored       -> pause: paste the SQL
    no SQL, a result stored, a follow-up command  -> follow-up
    no SQL, asks to explain SQL                   -> pause: paste the SQL (no call)
    anything else                                 -> scope check (one call: decline, ask, or help)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import dialects
import followup
import hitl
import schema
import smalltalk
import sqltext
from inputs import Incoming

_EXPLAIN_ASK = re.compile(r"\b(?:explain|what does|walk me through|review|check|problems?|issues?|smells?|"
                          r"wrong with|understand|analy[sz]e)\b", re.IGNORECASE)
_SQL_WORD = re.compile(r"\b(?:sql|query|queries|statement|select|update|delete|insert|merge|cte|join)\b",
                       re.IGNORECASE)
_WRITE_ASK = re.compile(r"\b(?:write|generate|build|give me|make|design)\b", re.IGNORECASE)
OVER_CAP_NOTE = ("Your SQL was over 30,000 characters; I read the statements in the first 30,000 and skipped "
                 "the rest.")


@dataclass(frozen=True)
class Route:
    kind: str                                   # reply | pause | run | followup | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    parsed: sqltext.Parsed | None = None
    hints: tuple[str, ...] = ()
    command: followup.Command | None = None
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, has_result: bool) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    extracted = sqltext.extract(incoming.text)
    if extracted.sql:
        return _with_sql(incoming, extracted, has_result)
    if has_result:
        command = followup.parse(incoming.text)
        return Route("followup", command=command) if command else Route("scope")
    if extracted.hints or (_EXPLAIN_ASK.search(incoming.text) and _SQL_WORD.search(incoming.text)
                           and not _WRITE_ASK.search(incoming.text)):
        return Route("pause", pending=need_sql(incoming, extracted.request, "\n".join(extracted.hints)))
    return Route("scope")


def _with_sql(incoming: Incoming, extracted: sqltext.Extracted, has_result: bool) -> Route:
    parsed = sqltext.parse(extracted, dialects.hinted(incoming.text))
    statements = parsed.parsed.statements if parsed.parsed is not None else ()
    if statements and all(schema.is_ddl(s) for s in statements):
        if has_result:
            return Route("followup", command=followup.Command("schema", incoming.text))
        extra = "\n".join([extracted.sql, *extracted.hints])
        return Route("pause", pending=need_sql(incoming, extracted.request, extra))
    notes = (OVER_CAP_NOTE,) if extracted.over_cap else ()
    return Route("run", parsed=parsed, hints=extracted.hints, notes=notes + incoming.notes)


def need_sql(incoming: Incoming, request: str, extra: str = "") -> hitl.Pending:
    return hitl.Pending(hitl.NEED_SQL, request.strip()[:500], extra[:30_000], incoming.wants_json)


def plan_resume(answer: hitl.Answer, incoming: Incoming) -> Route:
    if answer.action != "run":
        return Route("reply", answer.reply)
    return plan_turn(Incoming(answer.text, incoming.policy, incoming.wants_json, incoming.notes), has_result=False)
