"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                              -> canned intro (no call)
    a follow-up command on the stored result           -> follow-up (no call, or one for "show the rows")
    a pasted write statement / a request to change data -> fixed decline (no call)
    a pasted SELECT + "slow / optimize / faster ..."    -> optimize ("explain this" -> findings only, no call)
    a question + a schema (pasted now or stored)       -> write
    only a schema                                      -> stored, ask for the question (no call)
    a SQL-ish question, no schema anywhere             -> pause: paste the schema (no call)
    anything else                                      -> classify (one call: decline or ask for the schema)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import dialect as dialect_mod
import followup
import request as request_mod
import schema_parse
import smalltalk
from inputs import Incoming
from schema import Schema

_LINT_ONLY = re.compile(r"\b(?:explain|what does|walk me through|understand|what is this doing)\b", re.IGNORECASE)
_OPTIMIZE = re.compile(r"\b(?:slow|optimi[sz]e|faster|speed|takes|perf\w*|tune|improve|rewrite|better|quick\w*)\b",
                       re.IGNORECASE)
_WRITE_INTENT = re.compile(r"(?:^|[.!?]\s+|\n)\s*(?:please\s+|can you\s+|could you\s+|now\s+|i want to\s+|help me\s+)?"
                           r"(?:write (?:a |an |the )?(?:query|sql|statement) (?:to|that)\s+)?"
                           r"(?:delete|remove|update|insert|drop|truncate|alter|purge|mark|set)\b", re.IGNORECASE)
_SQLISH = re.compile(r"\b(?:sql|query|queries|select|table|tables|join|schema|column|columns|database|db|rows?|"
                     r"group by|count of|sum of|per (?:month|day|week|customer|region))\b", re.IGNORECASE)
SCHEMA_STORED = ("Got your schema ({n} tables: {names}). Ask a question about it, for example \"monthly revenue by "
                 "region\", or paste a slow query to optimize.")


@dataclass(frozen=True)
class Route:
    kind: str     # reply | followup | decline | optimize | write | stored | pause_schema | classify
    reply: str = ""
    command: followup.Command | None = None
    question: str = ""
    query: str = ""
    plan: str = ""
    lint_only: bool = False
    schema: Schema = field(default_factory=Schema)
    choice: dialect_mod.Choice | None = None
    pending_write: str = ""
    notes: tuple[str, ...] = ()


def stored_choice(state: dict) -> dialect_mod.Choice | None:
    saved = state.get("dialect")
    if not isinstance(saved, dict) or saved.get("name") not in dialect_mod.LABELS:
        return None
    return dialect_mod.Choice(saved["name"], bool(saved.get("assumed")), str(saved.get("how") or "assumed"))


def plan_turn(incoming: Incoming, state: dict) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    req = request_mod.split(incoming.text)
    pending_write = str(state.get("pending_write") or "")
    if state.get("envelope") and not req.query and not req.schema:
        command = followup.parse(req.words or incoming.text, bool(pending_write))
        if command is not None:
            return Route("followup", command=command, pending_write=pending_write)
    choice = _choice(req, state)
    stored = Schema.from_dict(state.get("schema"))
    pasted = schema_parse.parse(req.schema, choice.name) if req.schema else Schema()
    schema = stored.merged(pasted) if pasted else stored
    notes = tuple(f"Skipped schema statement that didn't parse: `{s}`" for s in pasted.skipped)
    if req.query:
        if _is_write_statement(req.query):
            return Route("decline", pending_write=req.query, schema=schema, choice=choice)
        lint_only = bool(_LINT_ONLY.search(req.words)) and not _OPTIMIZE.search(req.words)
        return Route("optimize", question=req.words, query=req.query, plan=req.plan, lint_only=lint_only,
                     schema=schema, choice=choice, notes=notes)
    if _WRITE_INTENT.search(req.words):
        return Route("decline", pending_write=req.words, schema=schema, choice=choice)
    if pending_write and followup.parse(req.words, True) == followup.Command("rows"):
        return _write_or_pause(_rows_question(pending_write), schema, choice, notes, pending_write)
    if schema and not req.words.strip():
        names = ", ".join(t.name for t in schema.tables[:8]) + (", ..." if len(schema.tables) > 8 else "")
        return Route("stored", SCHEMA_STORED.format(n=len(schema.tables), names=names), schema=schema, choice=choice,
                     notes=notes)
    if schema:
        return Route("write", question=req.words, schema=schema, choice=choice, notes=notes)
    if _SQLISH.search(req.words):
        return Route("pause_schema", question=req.words, choice=choice)
    return Route("classify", question=req.words or incoming.text, choice=choice)


def _choice(req: request_mod.Request, state: dict) -> dialect_mod.Choice:
    fresh = dialect_mod.choose(req.words, (req.query, req.schema))
    saved = stored_choice(state)
    return saved if fresh.assumed and saved is not None else fresh


def _is_write_statement(sql: str) -> bool:
    return bool(re.match(r"\s*(?:insert|update|delete|merge|drop|truncate|alter|create|grant|revoke)\b", sql,
                         re.IGNORECASE))


def _rows_question(pending_write: str) -> str:
    return ("Write the SELECT that lists the rows this change would touch (a read query only, no change): "
            + " ".join(pending_write.split())[:2000])


def _write_or_pause(question: str, schema: Schema, choice, notes, pending_write: str) -> Route:
    if schema:
        return Route("write", question=question, schema=schema, choice=choice, notes=notes,
                     pending_write=pending_write)
    return Route("pause_schema", question=question, choice=choice, pending_write=pending_write)


def rows_route(pending_write: str, state: dict, choice) -> Route:
    """The "show the rows it would touch" follow-up, after a decline."""
    return _write_or_pause(_rows_question(pending_write), Schema.from_dict(state.get("schema")), choice, (),
                           pending_write)
