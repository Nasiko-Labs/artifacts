"""Pure: what the user gave this turn. The first text part is the user's message (history prefix
removed); later text parts are org policy; a text file part (csv/json/plain, <= 5 MB) is data. The
message is split into the ask and the pasted data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_TYPES = frozenset({"text/plain", "text/csv", "text/tab-separated-values", "application/json", "text/markdown"})
TEXT_SUFFIXES = {".csv": "text/csv", ".tsv": "text/tab-separated-values", ".json": "application/json",
                 ".txt": "text/plain", ".md": "text/markdown"}
_FENCED = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
_BLANK = re.compile(r"\n\s*\n")
_JSON_MODE = re.compile(r"\b(?:as json|json only|in json)\b", re.IGNORECASE)
_DATA_LINE = re.compile(r"[,\t;|:=]\s*[-+($€£¥₹]?\d")


@dataclass(frozen=True)
class Incoming:
    text: str
    org: tuple[str, ...] = ()
    file_text: str = ""
    notes: tuple[str, ...] = ()
    as_json: bool = False


def current_message(raw: str) -> str:
    idx = raw.rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_parts(parts: Any, metadata: dict | None = None) -> Incoming:
    texts: list[str] = []
    files: list[str] = []
    notes: list[str] = []
    for part in parts or ():
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind == "raw":
            decoded, note = decode_file(part.filename or "the attached file", part.media_type, part.raw)
            files += [decoded] if decoded else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    as_json = bool(_JSON_MODE.search(user)) or (metadata or {}).get("output") == "json"
    return Incoming(text=user, org=tuple(t for t in texts[1:] if t.strip()), file_text="\n".join(files[:1]),
                    notes=tuple(notes), as_json=as_json)


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    kind = declared if declared in TEXT_TYPES else TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste its text instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; paste a filtered or aggregated version."
    return data.decode("utf-8", errors="replace"), None


def split_request(text: str) -> tuple[str, str]:
    """(ask, data). Data is the first fenced block; else what follows the first blank line; else the
    lines after a first line that isn't data; else, on one line, what follows the first colon."""
    body = (text or "").strip()
    fenced = _FENCED.search(body)
    if fenced:
        return (body[:fenced.start()] + body[fenced.end():]).strip(), fenced.group(1).strip()
    parts = _BLANK.split(body, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    lines = body.splitlines()
    if len(lines) > 1:
        if not lines[0].rstrip().endswith(":") and (_DATA_LINE.search(lines[0]) or _same_shape(lines[0], lines[1])):
            return "", body
        return lines[0].strip(), "\n".join(lines[1:]).strip()
    head, colon, tail = body.partition(":")
    if colon and _DATA_LINE.search(tail):
        return head.strip(), tail.strip()
    return body, ""


def _same_shape(first: str, second: str) -> bool:
    return any(first.count(d) >= 1 and first.count(d) == second.count(d) for d in ("\t", ";", ",", "|"))


def strip_json_cue(ask: str) -> str:
    return _JSON_MODE.sub("", ask or "").strip()
