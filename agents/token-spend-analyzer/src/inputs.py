"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part; later text parts (org policy, agent knowledge) are platform
  context, never data. With history the first part is "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used.
- A file part (proto `raw` bytes; CSV, JSON, JSONL or plain text, ≤ 5 MB) is decoded as the export.
- In text, the export is a fenced block, the first JSON value, or a CSV header line (≥ 3 delimited fields, the
  next line has as many); anything before it is the instruction. A previous step's envelope (JSON with a string
  `export`/`csv`/`text` field) is accepted in place of free text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

from detect import parse_json
from parse_csv import delimiter_of, fields

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
MIN_FIELDS = 3
HEADER_SCAN_LINES = 40
INSTRUCTION_MAX_CHARS = 300
TEXT_TYPES = frozenset({"text/csv", "text/plain", "application/json", "text/tab-separated-values",
                        "application/x-ndjson", "application/jsonl", "application/vnd.ms-excel"})
ENVELOPE_FIELDS = ("export", "csv", "text")
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCE = re.compile(r"```[a-zA-Z]*[ \t]*\n(.*?)\n?```", re.DOTALL)
_JSON_START = re.compile(r"(?:^|(?<=[\n :]))[{\[]")
MAX_JSON_TRIES = 3


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
    source: str
    kind: str = ""           # json | csv | "" (no export found)


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
    wants_json = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(_head(user)))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), files[0] if files else "", tuple(notes),
                    wants_json)


def _head(text: str) -> str:
    """The instruction part of a message (the data itself never switches modes)."""
    return split(text).instruction if text else ""


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    kind = (media_type or "").split(";")[0].strip().casefold()
    readable = kind in TEXT_TYPES or name.casefold().endswith((".csv", ".tsv", ".txt", ".json", ".jsonl"))
    if data is None or not readable:
        return None, f"I can't read {name} ({kind or 'unknown type'}); send the export as CSV or JSON text."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; aggregate it (daily by model and team) and send it again."
    return data.decode("utf-8", errors="replace"), None


def kind_of(source: str) -> str:
    stripped = (source or "").lstrip()
    return "json" if stripped[:1] in "{[" and parse_json(stripped) is not None else "csv"


def from_envelope(text: str) -> str | None:
    body = (text or "").strip()
    fenced = _FENCE.fullmatch(body)
    body = fenced.group(1).strip() if fenced else body
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return next((data[k] for k in ENVELOPE_FIELDS if isinstance(data.get(k), str) and data[k].strip()), None)


def find_header(lines: list[str]) -> int | None:
    for i, line in enumerate(lines[:HEADER_SCAN_LINES]):
        if not line.strip():
            continue
        delimiter = delimiter_of(line)
        width = fields(line, delimiter)
        if width < MIN_FIELDS:
            continue
        nxt = next((x for x in lines[i + 1:] if x.strip()), None)
        if nxt is not None and fields(nxt, delimiter) == width:
            return i
    return None


def _instruction(text: str) -> str:
    return text.strip().rstrip(":").strip()[:INSTRUCTION_MAX_CHARS]


def split(text: str) -> Request:
    """(instruction, export, kind) from one message."""
    enveloped = from_envelope(text)
    if enveloped is not None:
        inner = split(enveloped)
        return Request("", inner.source, inner.kind)
    body = (text or "").replace("\r\n", "\n")
    fenced = _FENCE.search(body)
    if fenced and fenced.group(1).strip():
        source = fenced.group(1).strip()
        return Request(_instruction(body[:fenced.start()]), source, kind_of(source))
    starts = [m.start() for m in _JSON_START.finditer(body)][:MAX_JSON_TRIES]
    for at in starts:
        candidate = body[at:].strip()
        if parse_json(candidate) is not None:
            return Request(_instruction(body[:at]), candidate, "json")
    lines = body.split("\n")
    at = find_header(lines)
    if at is None:
        return Request(_instruction(body), "")
    csv_lines = [x for x in lines[at:] if x.strip()]
    return Request(_instruction("\n".join(lines[:at])), "\n".join(csv_lines), "csv")
