"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part; later text parts (org policy, agent knowledge) are platform
  context, never data. With history the first part is "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used.
- A file part (proto `raw` bytes; text/csv, text/plain, text/tab-separated-values, application/json, ≤ 5 MB) is
  read as the export when the text holds none.
- A previous step's envelope (JSON with a `csv`, `export` or `text` field) is accepted in place of free text.
- Free-text hints parsed in code: "last N days" (window), "budget 40,000" and "repos: a, b".
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

import aggregate as agg
from export import parse_micros, split

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_REPOS = 5
TEXT_TYPES = frozenset({"text/csv", "text/plain", "application/json", "text/tab-separated-values",
                        "application/vnd.ms-excel", "text/markdown"})
ENVELOPE_FIELDS = ("csv", "export", "text")
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_WINDOW = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s*(day|days|d|week|weeks|w)\b", re.IGNORECASE)
_BUDGET = re.compile(r"\bbudget(?:\s+(?:is|of))?\s*[:=]?\s*[$€£]?\s*([\d][\d,]*(?:\.\d+)?)\s*(k)?\b", re.IGNORECASE)
_REPOS = re.compile(r"\brepo(?:s|sitory|sitories)?\s*[:=]\s*([^\n]+)", re.IGNORECASE)
_REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")
_FENCED = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    text: str                   # the user's current message, as typed
    instruction: str            # the words before the export (or the whole message without one)
    table: str                  # the export text (pasted, from an envelope, or from a file part), or ""
    notes: tuple[str, ...] = ()
    wants_json: bool = False


@dataclass(frozen=True)
class Hints:
    window_days: int | None
    budget_micros: int | None
    repos: tuple[str, ...]


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("", "", "")
    texts: list[str] = []
    files: list[str] = []
    notes: list[str] = []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            decoded, note = decode_file(part.filename or "the attached file", part.media_type,
                                        part.raw if kind == "raw" else None)
            files.extend([decoded] if decoded else [])
            notes.extend([note] if note else [])
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    return incoming(user, files[0] if files else "", tuple(notes),
                    str(metadata.get("output") or "").casefold() == "json")


def incoming(user: str, file_text: str = "", notes: Sequence[str] = (), json_meta: bool = False) -> Incoming:
    enveloped = from_envelope(user)
    parts = split(enveloped if enveloped is not None else user)
    instruction = "" if enveloped is not None else parts.instruction
    table = parts.table or (split(file_text).table if file_text else "")
    wants_json = json_meta or bool(_JSON_WORDS.search(instruction))
    return Incoming(user, instruction, table, tuple(notes), wants_json)


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    kind = (media_type or "").split(";")[0].strip().casefold()
    readable = kind in TEXT_TYPES or name.casefold().endswith((".csv", ".tsv", ".txt"))
    if data is None or not readable:
        return None, f"I can't read {name} ({kind or 'unknown type'}); send the export as CSV text."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; export it daily, grouped by service and one tag, and send it again."
    return data.decode("utf-8-sig", errors="replace"), None


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


def hints(instruction: str) -> Hints:
    return Hints(window_days(instruction), budget(instruction), repos(instruction))


def window_days(text: str) -> int | None:
    """"last 3 days" → 3, "past 2 weeks" → 14; clamped to 3-14. None when not asked."""
    m = _WINDOW.search(text or "")
    if not m:
        return None
    n = int(m.group(1)) * (7 if m.group(2).casefold().startswith("w") else 1)
    return agg.clamp_window(n)


def budget(text: str) -> int | None:
    m = _BUDGET.search(text or "")
    if not m:
        return None
    micros = parse_micros(m.group(1))
    if micros is None:
        return None
    return micros * 1000 if m.group(2) else micros


def repos(text: str) -> tuple[str, ...]:
    m = _REPOS.search(text or "")
    if not m:
        return ()
    names = [n.strip().strip("`'\".") for n in re.split(r"[,\s]+", m.group(1))]
    return tuple(dict.fromkeys(n for n in names if n and _REPO_NAME.match(n)))[:MAX_REPOS]
