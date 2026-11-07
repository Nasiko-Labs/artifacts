"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part; later text parts (org policy, agent knowledge) are platform
  context, never data. With history the first part is "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used.
- A file part (proto `raw` bytes; .tf, .json, .yaml, .template, text types; at most 5 MB) is decoded as IaC.
- A previous step's envelope (JSON with an `iac` / `source` / `template` / `code` / `text` string) is accepted
  in place of free text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

import detect

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_TYPES = frozenset({"text/plain", "application/json", "text/x-hcl", "application/x-yaml", "text/yaml",
                        "application/yaml", "text/x-yaml", "text/markdown", "application/hcl"})
SUFFIXES = (".tf", ".tfvars", ".hcl", ".json", ".yaml", ".yml", ".template", ".txt")
ENVELOPE_FIELDS = ("iac", "source", "template", "code", "text")
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    file_text: str = ""
    notes: tuple[str, ...] = ()
    wants_json: bool = False


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
            name = part.filename or "the attached file"
            decoded, note = decode_file(name, part.media_type, part.raw if kind == "raw" else None)
            files += [f"# file: {name}\n{decoded}"] if decoded else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants_json = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(instruction_of(user)))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), "\n".join(files), tuple(notes), wants_json)


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    kind = (media_type or "").split(";")[0].strip().casefold()
    readable = kind in TEXT_TYPES or kind.startswith("text/") or name.casefold().endswith(SUFFIXES)
    if data is None or not readable:
        return None, f"I can't read {name} ({kind or 'unknown type'}); send Terraform, plan JSON or CloudFormation as text."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; send the relevant files only."
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


def split(text: str) -> tuple[str, str]:
    """(instruction, IaC source) from one message; an envelope's IaC field counts as the source."""
    enveloped = from_envelope(text)
    if enveloped is not None:
        instruction, source = detect.split_request(enveloped)
        return instruction, source
    return detect.split_request(text)


def instruction_of(text: str) -> str:
    return split(text)[0] if text else ""
