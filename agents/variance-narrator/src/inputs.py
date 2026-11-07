"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts (org policy, agent knowledge) are platform
  instructions, never table data.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is read (the stored table comes from this agent's own checkpointer).
- File parts (inline bytes, CSV/TSV/markdown/plain/JSON) are decoded and read like pasted text.
- A previous step's envelope (JSON with `items[]` of {name, budget, actual}) is accepted as the table.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

from amounts import BAD, NA, parse_amount
from table import TOTAL_LABEL, Row

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_INPUT_CHARS = 100_000
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/csv", "text/tab-separated-values",
                        "application/json"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".tsv": "text/tab-separated-values",
                 ".json": "application/json"}
ENVELOPE_LISTS = ("items", "biggest_variances", "rows")
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    text: str
    policy: tuple[str, ...] = ()
    files: tuple[str, ...] = ()        # decoded file text
    wants_json: bool = False
    notes: tuple[str, ...] = ()


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts: list[str] = []
    files: list[str] = []
    notes: list[str] = []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            name = part.filename or "the attached file"
            decoded, note = decode_file(name, part.media_type, part.raw if kind == "raw" else None)
            files += [decoded] if decoded is not None else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), tuple(files), wants, tuple(notes))


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    kind = declared if declared in TEXT_TYPES else TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste its cells instead."
    return data.decode("utf-8", errors="replace"), None


def combined(incoming: Incoming) -> tuple[str, tuple[str, ...]]:
    """(message text with any files appended, notes), capped at the paste limit with a note."""
    text = "\n\n".join(p for p in (incoming.text, *incoming.files) if p.strip())
    if len(text) <= MAX_INPUT_CHARS:
        return text, incoming.notes
    note = f"Your input was over {MAX_INPUT_CHARS:,} characters, so I read only the first {MAX_INPUT_CHARS:,}."
    return text[:MAX_INPUT_CHARS], (*incoming.notes, note)


def envelope_rows(text: str) -> tuple[Row, ...] | None:
    """Rows from a previous step's envelope, or None when `text` is not one."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    entries = next((data[k] for k in ENVELOPE_LISTS if isinstance(data, dict) and isinstance(data.get(k), list)), None)
    if entries is None:
        return None
    rows = [_row(e) for e in entries if isinstance(e, dict)]
    return tuple(r for r in rows if r is not None)


def _row(entry: dict) -> Row | None:
    name = str(entry.get("name") or entry.get("line") or "").strip()
    if "budget" not in entry or "actual" not in entry:
        return None
    budget, actual = parse_amount(str(entry.get("budget", ""))), parse_amount(str(entry.get("actual", "")))
    if not name or {budget.status, actual.status} & {NA, BAD}:
        return None
    driver = str(entry.get("driver") or entry.get("explanation") or "").strip()[:300]
    return Row(name, budget.value, actual.value, driver=driver, is_total=bool(TOTAL_LABEL.search(name)))
