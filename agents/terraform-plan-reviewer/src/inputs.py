"""Pure: what the user gave this turn, read from the A2A message, and how it splits into the request and the plan.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never plan data.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (the earlier review comes from this agent's own checkpointer).
- File parts (text or JSON, up to 5 MB) are read as plan text; anything else is named in a note.
- A previous step's envelope (JSON with `plan`/`terraform_plan`/`text`, plus `environment`, `intent`) is accepted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_INSTRUCTION_CHARS = 600
ENVELOPE_PLAN_FIELDS = ("plan", "terraform_plan", "plan_text", "text")
TEXT_TYPES = ("text/", "application/json", "application/x-ndjson")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_PLANLIKE = re.compile(r"^\s*(?:#\s|Terraform |Plan: |No changes|\{|[~+\-]\s|-/\+|\+/-|<=\s|Note: |Warning: |"
                       r"Objects have changed|Changes to Outputs|Refreshing state|Acquiring state lock|[─\-]{5,}|"
                       r"(?:module|data)\.\S+|[a-z][a-z0-9]*_[a-z0-9_]+\.\S+:)")
_INLINE_SPLIT = re.compile(r"^(?P<head>[^\n]{1,300}?):\s+(?P<tail>(?:Terraform |\{|#\s|Plan: |No changes).*)$", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Request:
    instruction: str
    plan_text: str
    environment: str = ""    # stated in the request or the envelope: prod | staging | dev
    intent: str = ""


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts: list[str] = []
    files: list[tuple[str, str]] = []
    notes: list[str] = []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            name = part.filename or "the attached file"
            decoded, note = decode_file(name, part.media_type, part.raw if kind == "raw" else None)
            if decoded is not None:
                files.append((name, decoded))
            if note:
                notes.append(note)
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants, tuple(files), tuple(notes))


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    kind = (media_type or "").split(";")[0].strip().casefold()
    readable = not kind or kind.startswith(TEXT_TYPES) or name.endswith((".txt", ".json", ".tfplan.txt", ".log"))
    if data is None or not readable:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste the plan text instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; paste the plan text or a smaller file."
    return data.decode("utf-8", errors="replace"), None


def split_request(text: str, files: tuple[tuple[str, str], ...] = ()) -> Request:
    """The request line(s) before the plan, and the plan itself (files appended)."""
    enveloped = _from_envelope(text)
    if enveloped is not None:
        return enveloped
    stripped = (text or "").strip()
    inline = _INLINE_SPLIT.match(stripped)
    if inline and not _PLANLIKE.match(inline.group("head")):
        instruction, plan = inline.group("head").strip(), inline.group("tail")
    else:
        instruction, plan = _leading_request(stripped)
    plan = "\n\n".join(p for p in (plan, *(body for _, body in files)) if p.strip())
    return Request(instruction, plan)


def _leading_request(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if _PLANLIKE.match(line):
            head = "\n".join(lines[:idx]).strip()
            if len(head) <= MAX_INSTRUCTION_CHARS:
                return head, "\n".join(lines[idx:])
            return "", text
    return text, ""


def _from_envelope(text: str) -> Request | None:
    stripped = (text or "").strip()
    fenced = _FENCED.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{") or "resource_changes" in body[:5000] or '"format_version"' in body[:200]:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    plan = next((data[k] for k in ENVELOPE_PLAN_FIELDS if isinstance(data.get(k), str) and data[k].strip()), None)
    if plan is None:
        return None
    env = str(data.get("environment") or "")
    return Request("", plan.strip(), environment=env, intent=str(data.get("intent") or "").strip())
