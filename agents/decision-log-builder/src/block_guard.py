"""Code-enforced gate on the one document write an approval allows.

The card shows the exact block; the model then maps it onto the write tool's arguments. This checks that
call against what was approved: the same tool, the same document id (append) or the same new title
(create), the block verbatim — every character, case included; only line endings, trailing spaces and
blank lines at the very start or end are ignored — and no other free text. The approval also carries a
hash of the block, so a block altered between the card and the write is refused. Pure: no I/O.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

APPEND = "append"
CREATE = "create"


def canonical(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def block_hash(block: str) -> str:
    return hashlib.sha256(canonical(block).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WriteApproval:
    """One write: append `block` to document `doc_id`, or create a document titled `title` with it."""

    tool: str
    kind: str
    block: str
    digest: str
    doc_id: str = ""
    title: str = ""

    @staticmethod
    def of(tool: str, kind: str, block: str, doc_id: str = "", title: str = "") -> WriteApproval:
        return WriteApproval(tool=tool, kind=kind, block=block, digest=block_hash(block), doc_id=doc_id, title=title)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: object) -> WriteApproval | None:
        if not isinstance(data, Mapping) or not data.get("tool") or not data.get("block"):
            return None
        return WriteApproval(**{k: str(data.get(k) or "") for k in ("tool", "kind", "block", "digest", "doc_id",
                                                                     "title")})


def check(name: str, arguments: Mapping[str, Any], approval: WriteApproval | None) -> str | None:
    """None when the call is exactly the approved write; else the reason it is refused."""
    if approval is None:
        return "Refused: writing to the decision log needs the user's approval first. Nothing was written."
    if name.casefold() != approval.tool.casefold():
        return f"Refused: the user approved writing with {approval.tool}, not {name}."
    if block_hash(approval.block) != approval.digest:
        return "Refused: the approved block was changed after approval. Nothing was written."
    values = _scalars(arguments)
    wanted = canonical(approval.block)
    if not any(canonical(v) == wanted for v in values):
        return ("Refused: the text in this call is not the block the user approved. Pass it verbatim, every "
                "character and line break.")
    target = _target_problem(values, approval)
    if target:
        return target
    title = _norm(approval.title)
    stray = [v for v in values if " " in v.strip() and canonical(v) != wanted and _norm(v) != title]
    if stray:
        return "Refused: this call carries text the user did not approve. Pass only the approved block and ids."
    return None


def _target_problem(values: list[str], approval: WriteApproval) -> str | None:
    if approval.kind == APPEND and approval.doc_id not in {v.strip() for v in values}:
        return f"Refused: the approved document is {approval.doc_id}; name exactly that document id."
    if approval.kind == CREATE and _norm(approval.title) not in {_norm(v) for v in values}:
        return f"Refused: the new document must be titled exactly {approval.title}."
    return None


def _scalars(value: object) -> list[str]:
    """Every string or number in the arguments, at any depth."""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, float) and value.is_integer():
        return [str(int(value))]
    if isinstance(value, (str, int, float)):
        return [str(value)]
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _scalars(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _scalars(v)]
    return []


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()
