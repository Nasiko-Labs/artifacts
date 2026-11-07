"""Pure: the pasted notes as numbered note entries n1..nk, with timestamps and ticket refs, and the short
leading instruction line ("Handoff at 17:30", "for the night team", "short") split off.

An entry starts at a bullet, a numbered line, a timestamped line, or after a blank line; any other line
continues the entry above it."""

from __future__ import annotations

import re
from dataclasses import dataclass

INSTRUCTION_MAX_CHARS = 200

_BULLET = re.compile(r"^\s*(?:[-*\u2022\u2013]|\d{1,3}[.)])\s+")
_STAMP = re.compile(
    r"^\s*(?:[-*\u2022]\s*)?\[?((?:\d{4}-\d{2}-\d{2}[ T])?\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?|\d{1,2}\s*[ap]\.?m\.?)\]?"
    r"(?=[\s:\-\u2013\]])", re.IGNORECASE)
_REF = re.compile(r"(?<![\w#])#\d{2,}\b|\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b")
_INSTRUCTION = re.compile(
    r"\b(?:hand-?\s?off|hand over|as of|next shift|night team|day team|morning team|evening team|"
    r"shift|short(?:er)?|brief|end of (?:day|shift)|write (?:my|the|a)|from these notes)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Entry:
    id: str
    text: str
    order: int
    stamp: str | None = None
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "order": self.order, "stamp": self.stamp, "refs": list(self.refs)}

    @staticmethod
    def from_dict(data: dict) -> Entry:
        return Entry(str(data["id"]), str(data["text"]), int(data["order"]), data.get("stamp"),
                     tuple(data.get("refs") or ()))


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
    for offset, lines in enumerate(blocks):
        text = " ".join(lines)
        stamp = _STAMP.match(text)
        number = start + offset
        entries.append(Entry(f"n{number}", text, number, stamp.group(1) if stamp else None, find_refs(text)))
    return entries


def render_for_model(entries: list[Entry]) -> str:
    return "\n".join(f"[{e.id}] {e.text}" for e in entries)
