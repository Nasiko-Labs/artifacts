"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part; later text parts (org policy, agent knowledge) are platform
  context, never data. With history the first part is "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used.
- A file part (inline bytes; text/csv, text/plain, application/json, <= 5 MB) is read as the paste.
- The usage table starts at the first line naming a time column and a cost column; anything before it is the
  instruction. Commitments follow a line `--- commitments`. A previous step's envelope (JSON with
  `usage`/`csv`/`text`) is accepted in place of free text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

import tabular
from usage import OD_COLUMNS, OTHER_COST_COLUMNS, TIME_COLUMNS

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
INSTRUCTION_MAX_CHARS = 300
HEADER_SCAN_LINES = 60
TEXT_TYPES = frozenset({"text/csv", "text/plain", "application/json", "text/markdown"})
ENVELOPE_FIELDS = ("usage", "csv", "text")
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_COMMITMENTS = re.compile(r"^\s*-{2,}\s*commitments\b.*$", re.IGNORECASE | re.MULTILINE)
_FENCE_LINE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    file_text: str = ""
    notes: tuple[str, ...] = ()
    wants_json: bool = False


@dataclass(frozen=True)
class Request:
    instruction: str
    usage_csv: str = ""
    commitments_csv: str = ""


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts, files, notes = [], [], []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            decoded, note = decode_file(part.filename or "the attached file", part.media_type,
                                        part.raw if kind == "raw" else None)
            files += [decoded] if decoded else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants_json = (str(metadata.get("output") or "").casefold() == "json"
                  or bool(_JSON_WORDS.search(split(user).instruction)))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), files[0] if files else "", tuple(notes),
                    wants_json)


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    kind = (media_type or "").split(";")[0].strip().casefold()
    readable = kind in TEXT_TYPES or name.casefold().endswith((".csv", ".txt"))
    if data is None or not readable:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste the usage as CSV text."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; export daily or a single scope and send it again."
    return data.decode("utf-8", errors="replace"), None


def from_envelope(text: str) -> str | None:
    body = (text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return next((data[k] for k in ENVELOPE_FIELDS if isinstance(data.get(k), str) and data[k].strip()), None)


def _is_usage_header(line: str) -> bool:
    fields = {tabular.norm(f) for f in line.split(tabular.delimiter_of(line))}
    return bool(fields & set(TIME_COLUMNS)) and bool(fields & set(OD_COLUMNS + OTHER_COST_COLUMNS))


def _rows(lines: list[str]) -> str:
    return "\n".join(x for x in lines if x.strip() and not _FENCE_LINE.match(x))


def split(text: str) -> Request:
    """(instruction, usage CSV, commitments CSV) from one message."""
    enveloped = from_envelope(text)
    body = (enveloped if enveloped is not None else (text or "")).replace("\r\n", "\n").replace("﻿", "")
    marker = _COMMITMENTS.search(body)
    head, commitments = (body[:marker.start()], body[marker.end():]) if marker else (body, "")
    lines = head.split("\n")
    at = next((i for i, x in enumerate(lines[:HEADER_SCAN_LINES]) if _is_usage_header(x)), None)
    if at is None:
        instruction = "" if enveloped is not None else _rows(lines).strip()
        return Request(instruction[:INSTRUCTION_MAX_CHARS], "", _rows(commitments.split("\n")))
    instruction = _rows(lines[:at]).strip().rstrip(":").strip()
    return Request(instruction[:INSTRUCTION_MAX_CHARS], _rows(lines[at:]), _rows(commitments.split("\n")))
