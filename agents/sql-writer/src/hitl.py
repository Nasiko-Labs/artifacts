"""The questions this agent may pause on (the chat's request card renders them) and how answers are read.

    schema   "Paste the schema (CREATE TABLE statements or table(column type) lines)"   options: Cancel
    column   "Which column holds <concept>?"            options: candidate columns (<= 9) + Cancel
    join     "How should <a> and <b> be joined?"        options: candidate key paths (<= 9) + Cancel

One question per pause, at most two pauses per request. Reading fails closed: Cancel, or an answer that names
nothing in the schema, stops; nothing is guessed."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
import schema_parse
from inputs import current_message
from nasiko_hitl import question_metadata
from schema import Schema

METADATA_KEY = "sql_writer"
KINDS = ("schema", "column", "join")
MAX_PAUSES = 2
MAX_CHOICES = 9
CANCEL = "Cancel"
SCHEMA_HEADER = "Paste the schema (CREATE TABLE statements or table(column type) lines)"
NOT_SCHEMA = "That doesn't look like a schema, so I stopped here. Paste CREATE TABLE statements in a new message."
NOT_IN_SCHEMA = "That doesn't name a column in your schema, so I stopped here. Ask again with the column name."
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    question: str
    header: str
    options: tuple[str, ...] = ()
    concept: str = ""
    wants_json: bool = False
    asked: int = 1

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "question": self.question, "header": self.header,
                               "options": list(self.options), "concept": self.concept,
                               "wants_json": self.wants_json, "asked": self.asked}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in KINDS:
            return None
        return Pending(str(data["kind"]), str(data.get("question") or ""), str(data.get("header") or ""),
                       tuple(str(o) for o in data.get("options") or ()), str(data.get("concept") or ""),
                       bool(data.get("wants_json")), int(data.get("asked") or 1))


@dataclass(frozen=True)
class Answer:
    action: str              # run | cancel
    text: str = ""           # the request to rerun with
    reply: str = ""


def schema_question(question: str, wants_json: bool, asked: int = 1) -> Pending:
    return Pending("schema", question[:2000], SCHEMA_HEADER, (), "", wants_json, asked)


def column_question(question: str, concept: str, options: list[str], wants_json: bool, asked: int) -> Pending:
    header = f"Which column holds {concept.strip()[:120] or 'that value'}?"
    return Pending("column", question[:2000], header, tuple(options[:MAX_CHOICES]), concept[:120], wants_json, asked)


def join_question(question: str, a: str, b: str, options: list[str], wants_json: bool, asked: int) -> Pending:
    header = f"How should {a[:60]} and {b[:60]} be joined?"
    return Pending("join", question[:2000], header, tuple(options[:MAX_CHOICES]), "", wants_json, asked)


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    options = [(o[:200], "") for o in pending.options] + [(CANCEL, "")]
    text = prompts.NEED_SCHEMA if pending.kind == "schema" else pending.header
    return f"{text} Or cancel.", question_metadata(pending.header, options, allow_custom_input=True)


def interpret(pending: Pending, raw_answer: str, schema: Schema) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or _CANCEL.match(plain):
        return Answer("cancel", reply=prompts.CANCELLED)
    if pending.kind == "schema":
        if not schema_parse.has_schema(answer):
            return Answer("cancel", reply=NOT_SCHEMA)
        return Answer("run", text=f"{pending.question}\n\n{answer}")
    chosen = next((o for o in pending.options if o.casefold() == plain), None)
    if chosen is None and not _names_schema(answer, schema):
        return Answer("cancel", reply=NOT_IN_SCHEMA)
    if pending.kind == "column":
        hint = f"Use the column {chosen or answer} for {pending.concept or 'that value'}."
    else:
        hint = f"Join on {chosen or answer}."
    return Answer("run", text=f"{pending.question}\n{hint}")


def _names_schema(answer: str, schema: Schema) -> bool:
    words = {w.casefold() for w in re.findall(r"[A-Za-z_]\w*", answer)}
    return any(c.name.casefold() in words for t in schema.tables for c in t.columns)
