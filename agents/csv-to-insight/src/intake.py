"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                         → canned intro (no call)
    a table in the message, a file, an envelope   → analyse it (≤ 4 calls)
    a result stored ─┬─ re-render command         → render (no call)
                     ├─ "also show X by Y"        → one extra analysis (1 call)
                     ├─ "drill into <value>"      → filter + analyse (≤ 4 calls)
                     └─ any other question        → analyse the stored table with it (≤ 4 calls)
    nothing stored ─┬─ asks for an analysis       → pause: paste the CSV (no call)
                    └─ anything else              → one scope-checked call (declined, or pause)
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

import followup
import hitl
import prompts
import smalltalk
from inputs import Incoming
from load import from_envelope, split_message

QUESTION_MAX_CHARS = 500
_ANALYSIS_ASK = re.compile(r"\b(?:csv|spreadsheet|table|dataset|data ?set|analy[sz]e|analysis|insights?|"
                           r"findings?|chart|my data|this data)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                    # reply | pause | analyze | rerender | extra | drill | question | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    source: str = ""              # the table text
    delimiter: str = ","
    question: str = ""
    view: str = "default"
    notes: tuple[str, ...] = ()
    allow_pause: bool = True


def plan_turn(incoming: Incoming, has_table: bool) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    found = find_table(incoming)
    if found is not None:
        question, source, delimiter = found
        return Plan("analyze", source=source, delimiter=delimiter, question=question, notes=incoming.notes)
    text = prompts.strip_markers(incoming.text).strip()[:QUESTION_MAX_CHARS]
    if has_table:
        return _on_stored(text, incoming)
    if _ANALYSIS_ASK.search(text):
        return Plan("pause", pending=hitl.Pending(hitl.NEED_SOURCE, question=text), notes=incoming.notes)
    return Plan("scope", question=text, notes=incoming.notes)


def plan_resume(answer: hitl.Answer, pending: hitl.Pending) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_SOURCE_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    if pending.kind == hitl.MISSING_COLUMN:
        return Plan("analyze", source=answer.source, delimiter=pending.delimiter, question=answer.question,
                    allow_pause=False)
    found = find_table(Incoming(answer.source))
    if found is None:
        return Plan("reply", prompts.NO_SOURCE_GIVEN_UP)
    question, source, delimiter = found
    joined = " ".join(q for q in (answer.question, question) if q).strip()
    return Plan("analyze", source=source, delimiter=delimiter, question=joined)


def find_table(incoming: Incoming) -> tuple[str, str, str] | None:
    """(question, table text, delimiter) from a file part, an envelope or the pasted text."""
    text = prompts.strip_markers(incoming.text)
    for _, file_text in incoming.files:
        _, table, delimiter = split_message(prompts.strip_markers(file_text))
        if table:
            return text.strip()[:QUESTION_MAX_CHARS], table, delimiter
    enveloped = from_envelope(text)
    if enveloped is not None:
        return "", to_csv(enveloped.header, enveloped.rows), ","
    question, table, delimiter = split_message(text)
    if not table:
        return None
    return question[:QUESTION_MAX_CHARS], table, delimiter


def to_csv(header, rows) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().rstrip("\n")


def _on_stored(text: str, incoming: Incoming) -> Plan:
    command = followup.parse_command(text)
    if command is not None:
        return Plan("rerender", view=command, notes=incoming.notes)
    if followup.is_also(text):
        return Plan("extra", question=text, notes=incoming.notes)
    return Plan("question", question=text, notes=incoming.notes)
