"""Pure: what the user gave this turn, read from the A2A message, and how it splits into diff, context, intent.

- The user's own words are the FIRST text part; with chat history only the text after the last
  "Current message:" marker is used. Later text parts are org policy and agent knowledge: never the request.
- A text-type file part (diff, patch, markdown, json, csv, plain, <= 5 MB) is read as pasted text under a
  `# file: <name>` label.
- Context starts at a line such as `context:`, `repo context:`, `grep output:` or `--- context ---`, or at a
  fenced block right after a line mentioning context or grep. Another agent's envelope gives `items[].diff`
  or `summary` as the diff.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_CONTEXT_CHARS = 30_000
MAX_INTENT_CHARS = 200
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "application/json", "text/csv", "text/x-diff",
                        "text/x-patch", "application/x-patch", "application/x-diff"})
TEXT_SUFFIXES = (".diff", ".patch", ".txt", ".md", ".json", ".csv")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_CONTEXT_LINE = re.compile(r"^(?:repo(?:sitory)? context|context|grep output|grep results?)\s*:\s*(.*)$",
                           re.IGNORECASE)
_CONTEXT_RULE = re.compile(r"^-{2,}\s*context\s*-{2,}\s*$", re.IGNORECASE)
_CONTEXT_WORD = re.compile(r"\b(?:context|grep)\b", re.IGNORECASE)
_FENCE = re.compile(r"^\s*```[\w-]*\s*$")
_DIFF_START = re.compile(r"^(?:diff --git |--- |@@+ -\d|index [0-9a-f]+\.\.)")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Request:
    diff: str
    context: str = ""
    intent: str = ""


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
            name = part.filename or "attached file"
            text, note = decode_file(name, part.media_type, part.raw if kind == "raw" else None)
            files += [f"# file: {name}\n{text}"] if text is not None else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    if files:
        user = "\n\n".join([user, *files]) if user.strip() else "\n\n".join(files)
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user[:2_000]))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants, tuple(notes))


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    readable = declared in TEXT_TYPES or PurePath(name).suffix.casefold() in TEXT_SUFFIXES
    if data is None or not readable:
        return None, f"I can't read {name} ({declared or 'unknown type'}); paste its text instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB, so I skipped it."
    return data.decode("utf-8", errors="replace"), None


def from_envelope(text: str) -> str | None:
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    diffs = [i["diff"] for i in data.get("items") or [] if isinstance(i, dict) and isinstance(i.get("diff"), str)]
    if diffs:
        return "\n".join(diffs)
    summary = data.get("summary")
    return summary if isinstance(summary, str) and summary.strip() else None


def split_request(text: str) -> Request:
    enveloped = from_envelope(text)
    if enveloped is not None:
        return Request(diff=enveloped)
    lines = (text or "").replace("\r\n", "\n").split("\n")
    cut, first_context = _context_start(lines)
    body, context = lines[:cut], ([first_context] if first_context else []) + lines[cut + 1:]
    start = next((i for i, ln in enumerate(body) if _DIFF_START.match(ln)), len(body))
    intent = " ".join(ln.strip() for ln in body[:start] if ln.strip() and not _FENCE.match(ln))
    diff = "\n".join(ln for ln in body[start:] if not _FENCE.match(ln))
    context_text = "\n".join(ln for ln in context if not _FENCE.match(ln)).strip()
    return Request(diff=diff.strip("\n"), context=context_text[:MAX_CONTEXT_CHARS],
                   intent=_JSON_WORDS.sub("", intent).strip(" :")[:MAX_INTENT_CHARS])


def _context_start(lines: list[str]) -> tuple[int, str]:
    """(index of the context marker line, text after the marker on that line); len(lines) when none."""
    diff_start = next((i for i, ln in enumerate(lines) if _DIFF_START.match(ln)), len(lines))
    for i, line in enumerate(lines):
        if line.startswith(("+", "-", " ", "@")) and not _CONTEXT_RULE.match(line):
            continue
        marker = _CONTEXT_LINE.match(line)
        if marker:
            return i, marker.group(1).strip()
        if _CONTEXT_RULE.match(line):
            return i, ""
        if _CONTEXT_WORD.search(line) and i + 1 < len(lines) and _FENCE.match(lines[i + 1]) and i > diff_start:
            return i, ""
    return len(lines), ""

