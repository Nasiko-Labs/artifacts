"""Pure: the two pauses (which value column; chart or diagram), one question per pause and at most one
pause per request, and reading the answer back. The pause lives on the A2A task's metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_hitl import question_metadata

METADATA_KEY = "chart_maker"
MAX_COLUMN_OPTIONS = 8
MAX_LISTED = 12
MAX_LABEL_BYTES = 200
CHART = "Chart my data"
PROCESS = "Diagram a process"
HIERARCHY = "Diagram a hierarchy"
CANCEL_WORDS = ("cancel", "stop", "never mind", "nevermind")


@dataclass(frozen=True)
class Pending:
    kind: str                         # column | mode
    text: str                         # the whole original message (ask + data)
    org: tuple[str, ...] = ()
    as_json: bool = False
    columns: tuple[str, ...] = ()     # column pause: the value columns offered
    title: str = ""                   # column pause: the model's title, checked again on resume
    model_type: str | None = None
    x: str = ""

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "text": self.text, "org": list(self.org), "as_json": self.as_json,
                               "columns": list(self.columns), "title": self.title, "model_type": self.model_type,
                               "x": self.x}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = metadata.get(METADATA_KEY) if metadata else None
        if not isinstance(data, Mapping) or data.get("kind") not in ("column", "mode"):
            return None
        if not isinstance(data.get("text"), str):
            return None
        return Pending(kind=data["kind"], text=data["text"], org=tuple(str(o) for o in data.get("org") or ()),
                       as_json=bool(data.get("as_json")), columns=tuple(str(c) for c in data.get("columns") or ()),
                       title=str(data.get("title") or ""), model_type=data.get("model_type"), x=str(data.get("x") or ""))


def question(pending: Pending) -> tuple[str, dict]:
    if pending.kind == "column":
        names = [c for c in pending.columns if 0 < len(c.encode("utf-8")) <= MAX_LABEL_BYTES][:MAX_COLUMN_OPTIONS]
        more = len(pending.columns) - MAX_LISTED
        lead = ("Your data has several value columns: " + ", ".join(pending.columns[:MAX_LISTED])
                + (f" and {more} more" if more > 0 else "") + ". Which should I chart? Pick one, or type a "
                "column name or \"all as stacked\".")
        return lead, question_metadata("Which value should I chart?", [(n, "") for n in names])
    lead = "I can't tell whether you want a chart of data or a diagram. What should I draw?"
    return lead, question_metadata("What should I draw?", [(CHART, ""), (PROCESS, ""), (HIERARCHY, "")])


def is_cancel(answer: str) -> bool:
    return answer.strip().casefold().rstrip(".!") in CANCEL_WORDS or not answer.strip()


def column_answer(pending: Pending, answer: str) -> tuple[tuple[str, ...], bool] | None:
    """(value columns, stacked) for the answer, or None when it names no offered column."""
    text = answer.strip().casefold()
    if text in ("all as stacked", "all stacked", "stacked", "all, stacked"):
        return pending.columns, True
    if text in ("all", "all of them", "every column", "all columns"):
        return pending.columns, False
    chosen = tuple(c for c in pending.columns if c.casefold() == text)
    if chosen:
        return chosen, False
    named = tuple(c for c in pending.columns if c.casefold() in text)
    return (named, False) if named else None


def mode_answer(answer: str) -> str:
    """chart | flow | tree | other (custom text, taken as a further instruction)."""
    text = answer.strip().casefold()
    if text == CHART.casefold():
        return "chart"
    if text == PROCESS.casefold():
        return "flow"
    if text == HIERARCHY.casefold():
        return "tree"
    return "other"
