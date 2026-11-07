"""Code-enforced gate on every MCP tool that changes something (pattern copied from the hardened kit;
owned by this agent, rewritten for its two writes: append one sheet row, append one doc paragraph).

The prompt asks the model to make exactly the approved write, but a prompt is a request, not a
guarantee, and tool output is untrusted text. So any tool whose name reads as a write is refused here
unless the user approved that exact write on the approval pause, and only when the call is exactly it:
the same tool, the same spreadsheet or document id, and

- sheet: exactly one row of cells, equal cell by cell to the approved row (numbers compared by value);
- doc: every free-text value equal to the approved paragraph, through a tool that appends (never one
  that replaces or deletes).

One approval covers one write. Pure: decisions only, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "append",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write", "batch",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify", "replace", "clear", "overwrite",
})
DESTRUCTIVE = frozenset({"replace", "delete", "remove", "clear", "overwrite", "trash"})
_PLACE_KEY_TOKENS = frozenset({"range", "sheet", "tab", "title", "name", "option", "dimension", "location"})
_ID_OWNERS = frozenset({"spreadsheet", "document", "doc", "file"})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Approval:
    """What the user approved: one append, through one named tool, to one sheet or doc."""

    kind: str                       # "sheet" | "doc"
    tool: str
    target_id: str
    values: tuple[str, ...] = ()    # sheet: the row
    text: str = ""                  # doc: the paragraph

    def to_dict(self) -> dict:
        return {"kind": self.kind, "tool": self.tool, "target_id": self.target_id, "values": list(self.values),
                "text": self.text}

    @staticmethod
    def from_dict(data: object) -> Approval | None:
        if not isinstance(data, dict) or not data.get("tool") or not data.get("target_id"):
            return None
        return Approval(kind=str(data.get("kind") or ""), tool=str(data["tool"]), target_id=str(data["target_id"]),
                        values=tuple(str(v) for v in data.get("values") or ()), text=str(data.get("text") or ""))


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    is_write: bool
    reason: str = ""


def name_tokens(tool_name: str) -> tuple[str, ...]:
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    return any(token in WRITE_VERBS for token in name_tokens(tool_name))


def check(tool_names: Sequence[str], arguments: Sequence[dict], approval: Approval | None) -> GuardDecision:
    writes = [(n, a) for n, a in zip(tool_names, arguments) if is_write_tool(n)]
    if not writes:
        return GuardDecision(allowed=True, is_write=False)
    names = ", ".join(n for n, _ in writes)
    if approval is None:
        return _refused(f"Refused: {names} changes the user's files and needs their explicit approval first. "
                        "Nothing was written.")
    if len(writes) > 1:
        return _refused(f"Refused: one approval covers one write, but this call makes {len(writes)} ({names}).")
    name, args = writes[0]
    problem = _mismatch(name, args, approval)
    return _refused(problem) if problem else GuardDecision(allowed=True, is_write=True)


def _refused(reason: str) -> GuardDecision:
    return GuardDecision(allowed=False, is_write=True, reason=reason)


def _mismatch(name: str, args: dict, approval: Approval) -> str | None:
    if name.casefold() != approval.tool.casefold():
        return f"Refused: the user approved writing with {approval.tool}, not {name}. Use the approved tool."
    leaves = _leaves(args)
    ids = [str(v) for k, v in leaves if isinstance(v, str) and _is_id_key(k)]
    if approval.target_id not in [str(v) for _, v in leaves] or any(i != approval.target_id for i in ids):
        return (f"Refused: this call does not write to the approved {approval.kind} ({approval.target_id}) alone. "
                "Use exactly that id.")
    if approval.kind == "doc":
        return _doc_problem(name, leaves, approval)
    return _sheet_problem(args, leaves, approval)


def _doc_problem(name: str, leaves: list[tuple[str, Any]], approval: Approval) -> str | None:
    if DESTRUCTIVE & set(name_tokens(name)):
        return "Refused: a document is only ever appended to, never replaced or cleared."
    texts = [v for _, v in leaves if isinstance(v, str) and _WHITESPACE.search(v.strip())]
    wanted = _normalise(approval.text)
    if not texts or any(_normalise(t) != wanted for t in texts):
        return "Refused: the text in this call is not the paragraph the user approved. Use it verbatim."
    return None


def _sheet_problem(args: dict, leaves: list[tuple[str, Any]], approval: Approval) -> str | None:
    rows = _rows(args)
    if len(rows) != 1:
        return f"Refused: one approval covers one row, but this call carries {len(rows)}."
    if not _same_row(rows[0], approval.values):
        return "Refused: the row in this call is not the row the user approved. Use the approved values verbatim."
    in_row = {id(c) for c in rows[0]}
    stray = [k or "?" for k, v in leaves if isinstance(v, str) and id(v) not in in_row
             and _WHITESPACE.search(v.strip()) and not (_PLACE_KEY_TOKENS & set(name_tokens(k)))
             and v not in approval.values]
    if stray:
        return f"Refused: this call carries text the user did not approve (in {', '.join(sorted(set(stray)))})."
    return None


def _rows(value: Any) -> list[list[Any]]:
    """Every list of scalar cells (a row), at any depth."""
    if isinstance(value, dict):
        return [r for v in value.values() for r in _rows(v)]
    if isinstance(value, list):
        if value and all(_is_scalar(v) for v in value):
            return [value]
        return [r for v in value for r in _rows(v)]
    return []


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _same_row(cells: Sequence[Any], approved: Sequence[str]) -> bool:
    got = [_cell(c) for c in cells]
    want = list(approved)
    while got and got[-1] == "":
        got.pop()
    while want and want[-1] == "":
        want.pop()
    return len(got) == len(want) and all(_cell_equal(g, w) for g, w in zip(got, want))


def _cell(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _cell_equal(got: str, want: str) -> bool:
    if _normalise(got) == _normalise(want):
        return True
    try:
        return Decimal(got.replace(",", "")) == Decimal(want.replace(",", ""))
    except InvalidOperation:
        return False


def _is_id_key(key: str) -> bool:
    """`spreadsheet_id`, `documentId`, `file_id` or a bare `id` (not a tab's numeric `sheet_id`)."""
    tokens = name_tokens(key)
    return tokens == ("id",) or (len(tokens) > 1 and tokens[-1] == "id" and bool(_ID_OWNERS & set(tokens)))


def _leaves(value: Any, key: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        return [leaf for k, v in value.items() for leaf in _leaves(v, str(k))]
    if isinstance(value, list):
        return [leaf for v in value for leaf in _leaves(v, key)]
    return [(key, value)]


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text or "").strip().casefold()
