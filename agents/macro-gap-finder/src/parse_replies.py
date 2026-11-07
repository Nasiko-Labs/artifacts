"""Pure: a paste -> replies (+ existing macros + settings lines). First matching shape wins: JSON (an array,
or an envelope with `items[]`), CSV/TSV with a reply column, blocks split by `---` / `===` / `Reply:` headers,
plain blank-line blocks, else one reply per line (lines under 25 chars dropped)."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass

from sections import split_sections

MAX_REPLY_CHARS = 2_000
MIN_LINE_CHARS = 25
STRONG = ("reply", "response", "answer", "agent reply", "agent response", "reply text")
WEAK = ("body", "text", "message", "comment", "content")
ROLES = {"ticket": ("ticket", "ticket id", "id", "case", "case id", "ticket number"),
         "agent": ("agent", "author", "agent name", "assignee", "responder"),
         "subject": ("subject", "topic", "category"), "date": ("date", "created", "created at", "sent")}
ENVELOPE_TEXT = ("reply", "text", "response", "answer", "body")
_SEPARATOR = re.compile(r"^\s*(?:-{3,}|={3,})\s*$")
_HEADER = re.compile(r"^\s*(?:reply|agent|response)\s*#?\d*\s*:\s*(.*)$", re.IGNORECASE)
_BLANK = re.compile(r"\n\s*\n")
_FENCED = re.compile(r"\A```(?:json|csv)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Reply:
    id: str
    text: str
    ticket: str = ""
    agent: str = ""
    subject: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "ticket": self.ticket, "agent": self.agent, "subject": self.subject}

    @staticmethod
    def from_dict(data: dict) -> Reply:
        return Reply(**{k: str(data.get(k) or "") for k in ("id", "text", "ticket", "agent", "subject")})


@dataclass(frozen=True)
class Parsed:
    replies: tuple[Reply, ...] = ()
    shape: str = "none"
    ambiguous: tuple[str, ...] = ()
    dropped_short: int = 0
    macros: tuple[str, ...] = ()
    settings: tuple[tuple[str, int], ...] = ()


def _name(raw: str) -> str:
    return " ".join(str(raw).casefold().replace("_", " ").replace("-", " ").split())


def _numbered(rows: list[dict]) -> tuple[Reply, ...]:
    return tuple(Reply(id=f"r{n}", text=r["text"][:MAX_REPLY_CHARS], ticket=r.get("ticket", ""),
                       agent=r.get("agent", ""), subject=r.get("subject", ""))
                 for n, r in enumerate(rows, start=1))


def parse(text: str, reply_column: str | None = None) -> Parsed:
    body, macros, settings = split_sections(text)
    if not body:
        return Parsed(macros=macros, settings=settings)
    for attempt in (_json, lambda b: _csv(b, reply_column), _separated):
        result = attempt(body)
        if result is not None:
            return Parsed(result.replies, result.shape, result.ambiguous, result.dropped_short, macros, settings)
    result = _plain(body)
    return Parsed(result.replies, result.shape, (), result.dropped_short, macros, settings)


def column_roles(header: list[str], reply_column: str | None) -> tuple[dict[str, int], tuple[str, ...]]:
    names = [_name(h) for h in header]
    roles: dict[str, int] = {}
    if reply_column is not None:
        wanted = [i for i, n in enumerate(names) if n == _name(reply_column)]
        if wanted:
            roles["text"] = wanted[0]
    else:
        for tier in (STRONG, WEAK):
            hits = [i for i, n in enumerate(names) if n in tier]
            if len(hits) > 1:
                return {}, tuple(header[i].strip() for i in hits)
            if hits:
                roles["text"] = hits[0]
                break
    for role, aliases in ROLES.items():
        free = [i for i, n in enumerate(names) if n in aliases and i not in roles.values()]
        if free:
            roles[role] = free[0]
    return roles, ()


def _csv(body: str, reply_column: str | None) -> Parsed | None:
    first = body.splitlines()[0]
    delimiter = next((d for d in ("\t", ",", ";", "|") if d in first), None)
    if delimiter is None or "\n" not in body:
        return None
    rows = [r for r in csv.reader(io.StringIO(body), delimiter=delimiter) if any(c.strip() for c in r)]
    header = [h.strip() for h in rows[0]]
    if len(header) < 2 or any(not h or len(h) > 40 for h in header) or len(rows) < 2:
        return None
    roles, ambiguous = column_roles(header, reply_column)
    if ambiguous:
        return Parsed(shape="csv", ambiguous=ambiguous)
    if "text" not in roles:
        return None
    found = []
    for row in rows[1:]:
        cell = {role: (row[i].strip() if i < len(row) else "") for role, i in roles.items()}
        if cell["text"]:
            found.append(cell)
    return Parsed(_numbered(found), "csv", dropped_short=len(rows) - 1 - len(found))


def _json(body: str) -> Parsed | None:
    fenced = _FENCED.match(body)
    text = fenced.group(1) if fenced else body
    if not text.startswith(("[", "{")):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    rows = (data.get("items") or data.get("replies")) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None
    found = [row for row in (_from_object(r) for r in rows) if row]
    return Parsed(_numbered(found), "envelope" if isinstance(data, dict) else "json",
                  dropped_short=len(rows) - len(found))


def _from_object(row: object) -> dict | None:
    if isinstance(row, str):
        return {"text": row.strip()} if row.strip() else None
    if not isinstance(row, dict):
        return None
    keys = {_name(k): k for k in row}
    text_key = next((keys[k] for k in (*STRONG, *ENVELOPE_TEXT, *WEAK) if k in keys and isinstance(row[keys[k]], str)),
                    None)
    if text_key is None or not str(row[text_key]).strip():
        return None
    found = {"text": str(row[text_key]).strip()}
    for role, aliases in ROLES.items():
        key = next((keys[a] for a in aliases if a in keys and keys[a] != text_key), None)
        if key is not None and row[key] not in (None, ""):
            found[role] = str(row[key]).strip()
    return found


def _separated(body: str) -> Parsed | None:
    lines = body.split("\n")
    if not any(_SEPARATOR.match(line) or _HEADER.match(line) for line in lines):
        return None
    blocks: list[dict] = []
    current: dict = {"text": [], "agent": ""}
    for line in lines:
        header = _HEADER.match(line)
        if _SEPARATOR.match(line) or header:
            blocks.append(current)
            current = {"text": [], "agent": ""}
            rest = header.group(1).strip() if header else ""
            if header and line.strip().casefold().startswith("agent") and 0 < len(rest) <= 40 and "." not in rest:
                current["agent"] = rest
            elif rest:
                current["text"].append(rest)
        else:
            current["text"].append(line)
    blocks.append(current)
    found = [{"text": "\n".join(b["text"]).strip(), "agent": b["agent"]} for b in blocks]
    kept = [b for b in found if b["text"]]
    return Parsed(_numbered(kept), "blocks")


def _plain(body: str) -> Parsed:
    blocks = [b.strip() for b in _BLANK.split(body) if b.strip()]
    if len(blocks) > 1:
        return Parsed(_numbered([{"text": b} for b in blocks]), "plain")
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    kept = [line for line in lines if len(line) >= MIN_LINE_CHARS]
    return Parsed(_numbered([{"text": line} for line in kept]), "lines", dropped_short=len(lines) - len(kept))
