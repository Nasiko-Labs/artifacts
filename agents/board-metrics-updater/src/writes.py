"""Pure: one pending write (a sheet row or a doc paragraph), its approval card, and reading the human's
answer to it. Numbers come from code; a typed edit can never change them."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any, Literal

from nasiko_hitl import question_metadata
from send_guard import Approval

ADD = "Add"
APPEND = "Append"
SKIP = "Skip"
_YES = frozenset({"add", "append", "yes", "y", "ok", "okay", "approve", "approved", "go", "do it", "add it",
                  "append it", "confirm"})
_NO = frozenset({"skip", "no", "n", "cancel", "stop", "deny", "denied", "reject", "rejected", "decline", "don't",
                 "dont"})
_HEADER_NAME_MAX = 120
_DIGIT = re.compile(r"\d")

Decision = Literal["add", "skip", "edit"]


@dataclass(frozen=True)
class Write:
    kind: str                 # "sheet" | "doc"
    key: str                  # what the outcome is recorded under (e.g. "sheet:USD", "doc")
    tool: str
    target_id: str
    target_name: str
    tab: str = ""
    header: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    text: str = ""

    @property
    def label(self) -> str:
        return f"Row for {self.target_name}" if self.kind == "sheet" else f"Paragraph for {self.target_name}"

    def to_dict(self) -> dict:
        return {f.name: (list(getattr(self, f.name)) if isinstance(getattr(self, f.name), tuple)
                         else getattr(self, f.name)) for f in fields(Write)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Write:
        values = {f.name: data.get(f.name) for f in fields(Write)}
        return Write(**{k: (tuple(str(x) for x in v or ()) if k in ("header", "values") else str(v or ""))
                        for k, v in values.items()})


def approval_for(write: Write) -> Approval:
    return Approval(kind=write.kind, tool=write.tool, target_id=write.target_id, values=write.values,
                    text=write.text)


def _short(name: str) -> str:
    return name if len(name.encode("utf-8")) <= _HEADER_NAME_MAX else "this file"


def describe(write: Write) -> str:
    if write.kind == "doc":
        return "\n".join([f"Document: {write.target_name}", f"Via: {write.tool}",
                          "Appended at the end; nothing in the document is changed.", "", write.text])
    width = max(len(write.header), len(write.values))
    header = list(write.header) + [""] * (width - len(write.header))
    values = list(write.values) + [""] * (width - len(write.values))
    mapping = "\n".join(f"- {h or '(column ' + str(i + 1) + ')'}: {v or '(blank)'}"
                        for i, (h, v) in enumerate(zip(header, values)))
    tab = f" / {write.tab}" if write.tab else ""
    return f"Sheet: {write.target_name}{tab}\nVia: {write.tool}\nOne new row at the bottom:\n{mapping}"


def approval_question(write: Write) -> tuple[str, dict]:
    if write.kind == "doc":
        header, yes = f"Append this paragraph to {_short(write.target_name)}?", APPEND
        hint = "Reply Append or Skip."
    else:
        header, yes = f"Add this row to {_short(write.target_name)}?", ADD
        hint = "Reply Add or Skip. The numbers come from your payments account and can't be edited here."
    hitl = question_metadata(header, [(yes, "Write exactly this, once."), (SKIP, "Don't write it.")],
                             multi_select=False, allow_custom_input=True)
    return describe(write) + f"\n\n{hint}", hitl


def interpret_answer(answer: str) -> tuple[Decision, str]:
    """Add / Skip / anything else (an edit request). Empty is Skip: nothing is written by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _NO:
        return "skip", ""
    if word in _YES:
        return "add", ""
    return "edit", text


def is_number_edit(text: str) -> bool:
    return bool(_DIGIT.search(text or ""))
