"""The one question this agent may pause on (Q1 "Paste the SQL", rendered by the chat's request card) and how
an answer is read. Reading fails closed: Cancel, or text with no SQL in it, stops; nothing is guessed."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "query_explainer"
NEED_SQL = "need_sql"
HEADER = "Paste the SQL"
CANCEL = "Cancel"
NOT_SQL = ("That doesn't look like SQL, so I stopped here. Paste the query in a new message when you're ready.")
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str = NEED_SQL
    request: str = ""
    extra: str = ""          # schema lines or DDL already given, kept for the answer
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "request": self.request, "extra": self.extra,
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_SQL:
            return None
        return Pending(NEED_SQL, str(data.get("request") or ""), str(data.get("extra") or ""),
                       bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str              # run | cancel
    text: str = ""
    reply: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    return prompts.NEED_SQL + " Or cancel.", question_metadata(HEADER, [(CANCEL, "")], allow_custom_input=True)


def interpret(pending: Pending, raw_answer: str, has_sql) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or _CANCEL.match(plain):
        return Answer("cancel", reply=prompts.CANCELLED)
    combined = "\n".join(p for p in (pending.request, pending.extra, answer) if p.strip())
    if not has_sql(answer):
        return Answer("cancel", reply=NOT_SQL)
    return Answer("run", text=combined)
