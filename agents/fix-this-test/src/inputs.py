"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never the request and never evidence.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (earlier inputs and results come from the checkpointer).
- An A2A file part (inline text bytes) is accepted as pasted text: a source file becomes a `# file:` block.
- Another workflow step's envelope (JSON with items[].failure / items[].files, or summary) is accepted.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_PART_BYTES = 5 * 1024 * 1024
TEXT_MEDIA = ("text/", "application/json", "application/x-diff", "application/x-patch", "text/x-diff")
SOURCE_SUFFIXES = (".py", ".ini", ".cfg", ".toml")
MAX_ENVELOPE_LEAD_CHARS = 200  # a short instruction ("as json:") may precede the envelope
FENCE_OPEN, FENCE_CLOSE = "<<<INPUT>>>", "<<<END>>>"

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_NO_TEST_EDITS = re.compile(r"\b(?:don'?t|do not|never|without) (?:change|changing|edit|editing|modify|modifying|"
                            r"touch|touching) (?:the |any |my )?tests?\b", re.IGNORECASE)
_ONLY_EDIT = re.compile(r"\bonly (?:edit|change|modify|touch) `?([\w./-]+\.py)`?", re.IGNORECASE)
_EXPLAIN_ONLY = re.compile(r"\bexplain only\b|\bno edits\b|\bdon'?t (?:fix|edit)\b|^\s*why (?:does|did|is) "
                           r"(?:this|the|my) test fail", re.IGNORECASE)
_SMART = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, platform policy parts, file parts, and whether JSON was asked."""

    text: str
    org: tuple[str, ...] = ()
    attachments: tuple[str, ...] = ()
    wants_json: bool = False


@dataclass(frozen=True)
class Constraints:
    no_test_edits: bool = False
    only_edit: tuple[str, ...] = ()
    explain_only: bool = False

    def to_dict(self) -> dict:
        return {"no_test_edits": self.no_test_edits, "only_edit": list(self.only_edit),
                "explain_only": self.explain_only}

    @staticmethod
    def from_dict(data: Any) -> Constraints:
        data = data if isinstance(data, dict) else {}
        return Constraints(bool(data.get("no_test_edits")), tuple(str(p) for p in data.get("only_edit") or ()),
                           bool(data.get("explain_only")))


@dataclass(frozen=True)
class Envelope:
    failure: str = ""
    files: dict[str, str] = field(default_factory=dict)


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts = [p.text for p in message.parts if p.WhichOneof("content") == "text"]
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    attachments = tuple(a for a in (_attachment(p) for p in message.parts if p.WhichOneof("content") == "raw") if a)
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), attachments, wants)


def _attachment(part: Any) -> str | None:
    media = (part.media_type or "text/plain").casefold()
    if len(part.raw) > MAX_FILE_PART_BYTES or not media.startswith(TEXT_MEDIA):
        return None
    try:
        text = part.raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    name = (part.filename or "").strip()
    if name.casefold().endswith(SOURCE_SUFFIXES):
        return f"# file: {name}\n{text}"
    return text


def constraints(text: str) -> Constraints:
    return Constraints(no_test_edits=bool(_NO_TEST_EDITS.search(text or "")),
                       only_edit=tuple(m.group(1).lstrip("./") for m in _ONLY_EDIT.finditer(text or "")),
                       explain_only=bool(_EXPLAIN_ONLY.search(text or "")))


def from_envelope(text: str) -> Envelope | None:
    """Failure output and files from another step's envelope, or None when `text` is not one."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.search(stripped)
    body = fenced.group(1) if fenced else stripped
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end < start or start > MAX_ENVELOPE_LEAD_CHARS:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    failures, files = [], {}
    for item in data.get("items") or ():
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("failure"), str):
            failures.append(item["failure"])
        files.update(_envelope_files(item.get("files")))
    if not failures and isinstance(data.get("summary"), str):
        failures.append(data["summary"])
    if not failures and not files:
        return None
    return Envelope("\n".join(failures), files)


def _envelope_files(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items() if isinstance(v, str)}
    if isinstance(value, list):
        return {str(f["path"]): f["content"] for f in value
                if isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("content"), str)}
    return {}


def fence_text(text: str) -> str:
    """Pasted data, fenced; any marker inside the data is removed first so it cannot close the fence."""
    clean = (text or "").replace(FENCE_OPEN, "").replace(FENCE_CLOSE, "")
    return f"{FENCE_OPEN}\n{clean}\n{FENCE_CLOSE}"


def normalise(text: str) -> str:
    """For verbatim checks: NFKC, smart quotes to straight, whitespace collapsed."""
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", text or "").translate(_SMART)).strip()


def appears_in(needle: str, haystack: str) -> bool:
    small = normalise(needle)
    return bool(small) and small in normalise(haystack)
