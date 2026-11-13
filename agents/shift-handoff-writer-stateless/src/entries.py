"""Pure: the pasted notes as numbered note entries n1..nk, with timestamps and ticket refs, and the short
leading instruction line ("Handoff at 17:30", "for the night team", "short") split off.

An entry starts at a bullet, a numbered line, a timestamped line, or after a blank line; any other line
continues the entry above it. A run-on entry of 3 or more `;`-separated clauses (notes typed on one line) is
split into one entry per clause, so each update keeps its own status, cues and customer; the clauses of one line
share a `group`, and an item may not span sibling clauses (reconcile.py). A one-line request ("Write my shift
handoff from these notes: ...") has its instruction split off at the first colon."""

from __future__ import annotations

import re
from dataclasses import dataclass

INSTRUCTION_MAX_CHARS = 200
RUN_ON_MIN_CLAUSES = 3
RUN_ON_MIN_CLAUSE_WORDS = 3

_BULLET = re.compile(r"^\s*(?:[-*\u2022\u2013]|\d{1,3}[.)])\s+")
_STAMP = re.compile(
    r"^\s*(?:[-*\u2022]\s*)?\[?((?:\d{4}-\d{2}-\d{2}[ T])?\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?|\d{1,2}\s*[ap]\.?m\.?)\]?"
    r"(?=[\s:\-\u2013\]])", re.IGNORECASE)
_REF = re.compile(r"(?<![\w#])#\d{2,}\b|\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b")
_INSTRUCTION = re.compile(
    r"\b(?:hand-?\s?off|hand over|as of|next shift|night team|day team|morning team|evening team|"
    r"shift|short(?:er)?|brief|end of (?:day|shift)|write (?:my|the|a)|from these notes)\b", re.IGNORECASE)
_COLON_HEAD = re.compile(r"^([^\n:]{3,%d}):\s+(\S.*)$" % INSTRUCTION_MAX_CHARS, re.DOTALL)
NOTHING_PENDING = re.compile(
    r"^\W*(?:nothing(?: else)?(?: (?:pending|open|to report|outstanding|to hand over))?|no other (?:updates|items)|"
    r"all (?:quiet|good)|that'?s (?:it|all))\W*$", re.IGNORECASE)


@dataclass(frozen=True)
class Entry:
    id: str
    text: str
    order: int
    stamp: str | None = None
    refs: tuple[str, ...] = ()
    group: int | None = None           # set on the clauses of one run-on line (siblings share it)

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "order": self.order, "stamp": self.stamp, "refs": list(self.refs),
                "group": self.group}

    @staticmethod
    def from_dict(data: dict) -> Entry:
        return Entry(str(data["id"]), str(data["text"]), int(data["order"]), data.get("stamp"),
                     tuple(data.get("refs") or ()), data.get("group"))

    def is_sibling(self, other: Entry) -> bool:
        """Another clause of the same run-on line."""
        return self.group is not None and self.group == other.group and self.id != other.id


def is_nothing_pending(text: str) -> bool:
    """"nothing else pending", "all quiet": a closing remark, not an update."""
    return bool(NOTHING_PENDING.match(without_stamp(text)))


def ref_key(ref: str) -> str:
    """The merge key of a ref: `#4812`, `4812` and ` #4812 ` are the same ticket."""
    return ref.strip().lstrip("#").upper()


def find_refs(text: str) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for match in _REF.finditer(text or ""):
        seen.setdefault(ref_key(match.group(0)), match.group(0))
    return tuple(seen.values())


def without_stamp(text: str) -> str:
    """The entry's words without its leading bullet and timestamp."""
    stamp = _STAMP.match(text or "")
    rest = (text or "")[stamp.end():] if stamp else (text or "")
    return _BULLET.sub("", rest).lstrip(" ]:-\u2013")


def split_instruction(text: str) -> tuple[str, str]:
    """(instruction, notes). The first paragraph, or else the first line, is the instruction when it is
    short, is not itself a note line (bullet or timestamp) and reads like one."""
    stripped = (text or "").strip()
    for head, rest in _heads(stripped):
        if (len(head) <= INSTRUCTION_MAX_CHARS and not _BULLET.match(head) and not _STAMP.match(head)
                and _INSTRUCTION.search(head) and rest):
            return head.rstrip(":").strip(), rest
    return "", stripped


def _heads(text: str) -> list[tuple[str, str]]:
    out = []
    para = re.search(r"\n\s*\n", text)
    if para:
        out.append((text[:para.start()].strip(), text[para.end():].strip()))
    line = text.find("\n")
    if line > 0:
        out.append((text[:line].strip(), text[line + 1:].strip()))
    colon = _COLON_HEAD.match(text)
    if colon:
        out.append((colon.group(1).strip(), colon.group(2).strip()))
    return out


def parse(notes: str, start: int = 1) -> list[Entry]:
    """Note entries numbered from `start` (so an added note continues the numbering)."""
    blocks: list[list[str]] = []
    after_blank = True
    for line in (notes or "").replace("\r\n", "\n").split("\n"):
        if not line.strip():
            after_blank = True
            continue
        if after_blank or _BULLET.match(line) or _STAMP.match(line) or not blocks:
            blocks.append([line.strip()])
        else:
            blocks[-1].append(line.strip())
        after_blank = False
    entries = []
    texts = [(clause, index if len(clauses) > 1 else None)
             for index, lines in enumerate(blocks) for clauses in [_clauses(" ".join(lines))] for clause in clauses]
    for offset, (text, group) in enumerate(texts):
        stamp = _STAMP.match(text)
        number = start + offset
        entries.append(Entry(f"n{number}", text, number, stamp.group(1) if stamp else None, find_refs(text),
                             None if group is None else start + group))
    return entries


def _clauses(text: str) -> list[str]:
    """A run-on line of `;` clauses as one text per clause; anything else unchanged."""
    parts = [p.strip() for p in text.split(";")]
    if len(parts) < RUN_ON_MIN_CLAUSES or any(len(p.split()) < RUN_ON_MIN_CLAUSE_WORDS for p in parts):
        return [text]
    return parts


def render_for_model(entries: list[Entry]) -> str:
    return "\n".join(f"[{e.id}] {e.text}" for e in entries)
