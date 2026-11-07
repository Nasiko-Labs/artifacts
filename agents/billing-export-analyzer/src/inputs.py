"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part; later text parts (org policy, agent knowledge) are platform
  context, never data. With history the first part is "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used.
- A file part (proto `raw` bytes; text/csv, text/plain, application/json, ≤ 5 MB) is decoded as the export.
- The export is found in the text as its header line (≥ 3 delimited fields, the next line has as many);
  anything before it is the instruction. A previous step's envelope (JSON with `csv`/`export`/`text`) is
  accepted in place of free text.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

from frame import delimiter_of

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
MIN_FIELDS = 3
INSTRUCTION_MAX_CHARS = 300
TEXT_TYPES = frozenset({"text/csv", "text/plain", "application/json", "text/tab-separated-values",
                        "application/vnd.ms-excel"})
ENVELOPE_FIELDS = ("csv", "export", "text")
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_PNG_WORDS = re.compile(r"\b(?:with|as|send|attach)\s+(?:a\s+)?(?:png|chart file|image)\b|\bpng\b", re.IGNORECASE)
_FENCED = re.compile(r"\A```(?:json|csv)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    file_text: str = ""
    notes: tuple[str, ...] = ()
    wants_json: bool = False
    wants_png: bool = False


@dataclass(frozen=True)
class Request:
    instruction: str
    csv_text: str


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
    wants_png = str(metadata.get("chart") or "").casefold() == "png" or bool(_PNG_WORDS.search(_head(user)))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), files[0] if files else "", tuple(notes),
                    wants_json, wants_png)


def _head(text: str) -> str:
    """The instruction part of a message (the data itself never switches modes)."""
    return split(text).instruction if text else ""


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    kind = (media_type or "").split(";")[0].strip().casefold()
    readable = kind in TEXT_TYPES or name.casefold().endswith((".csv", ".tsv", ".txt"))
    if data is None or not readable:
        return None, f"I can't read {name} ({kind or 'unknown type'}); send the export as CSV text."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; pre-aggregate it (daily by service) and send it again."
    return data.decode("utf-8", errors="replace"), None


def from_envelope(text: str) -> str | None:
    stripped = (text or "").strip()
    fenced = _FENCED.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return next((data[k] for k in ENVELOPE_FIELDS if isinstance(data.get(k), str) and data[k].strip()), None)


def _fields(line: str, delimiter: str) -> int:
    return len(next(csv.reader([line], delimiter=delimiter), []))


def find_header(lines: list[str]) -> int | None:
    for i, line in enumerate(lines[:40]):
        if not line.strip():
            continue
        delimiter = delimiter_of(line)
        width = _fields(line, delimiter)
        if width < MIN_FIELDS:
            continue
        nxt = next((x for x in lines[i + 1:] if x.strip()), None)
        if nxt is not None and _fields(nxt, delimiter) == width:
            return i
    return None


def split(text: str) -> Request:
    """(instruction, CSV) from one message."""
    enveloped = from_envelope(text)
    body = enveloped if enveloped is not None else (text or "")
    body = _FENCED.sub(lambda m: m.group(1), body.strip()) if body.strip().startswith("```") else body
    lines = body.replace("\r\n", "\n").split("\n")
    at = find_header(lines)
    if at is None:
        return Request(body.strip()[:INSTRUCTION_MAX_CHARS] if enveloped is None else "", "")
    instruction = "\n".join(lines[:at]).strip().rstrip(":").strip()
    csv_lines = [x for x in lines[at:] if x.strip() and not x.strip().startswith("```")]
    return Request(instruction[:INSTRUCTION_MAX_CHARS], "\n".join(csv_lines))
