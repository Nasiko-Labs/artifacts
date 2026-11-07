"""Pure: what the user gave this turn and how it splits into diff, spec, standards and focus.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never the request and never evidence.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (the stored diff and result come from this agent's own checkpointer).
- A text file part (diff, patch, markdown, json, plain, <= 5 MB) is read as pasted text, labelled `# file: <name>`.
- Another agent's envelope (JSON with `items[].diff` or `summary`) is accepted as the diff text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_DIFF_CHARS = 100_000
MAX_SPEC_CHARS = 8_000
MAX_STANDARDS_CHARS = 6_000
MAX_FILE_BYTES = 5 * 1024 * 1024
MARKERS = re.compile(r"<<<\s*/?\s*[A-Z_]+\s*>>>")
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "application/json", "text/csv",
                        "text/x-diff", "text/x-patch", "application/x-diff", "application/x-patch"})
TEXT_SUFFIXES = (".diff", ".patch", ".txt", ".md", ".json", ".csv")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_LABEL = re.compile(r"\b(spec|issue|requirements|acceptance criteria|standards|conventions)\s*:\s*(.*)$",
                    re.IGNORECASE)
_FENCE = re.compile(r"^\s*```[\w-]*\s*$")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_AXES = ("security", "correctness", "readability", "architecture", "performance", "standards")
_ONLY_AXIS = re.compile(rf"\b(?:only|just)\s+(?:the\s+)?({'|'.join(_AXES)})\b|\b({'|'.join(_AXES)})\s+only\b",
                        re.IGNORECASE)
_NITS = re.compile(r"\bnits?\s+too\b|\binclude nits\b|\bwith nits\b", re.IGNORECASE)
_STRICT = re.compile(r"\bbe strict\b|\bstrict review\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words (plus decoded text files), policy parts, JSON wish, file notes."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Asks:
    """The non-diff part of a request: spec, standards and focus."""

    spec: str = ""
    standards: str = ""
    axis: str = ""          # one axis only, or "" for all
    nits: bool = False
    strict: bool = False
    spec_cut: bool = False
    standards_cut: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def strip_markers(text: str) -> str:
    return MARKERS.sub("", text or "")


def fence_text(name: str, text: str) -> str:
    """Untrusted text between markers; markers inside the text are removed first."""
    return f"<<<{name}>>>\n{strip_markers(text)}\n<<<END>>>"


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
            decoded, note = decode_file(part.filename or "attached file", part.media_type,
                                        part.raw if kind == "raw" else None)
            files.extend([decoded] if decoded else [])
            notes.extend([note] if note else [])
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    body = "\n\n".join([user, *files]) if files else user
    return Incoming(body, tuple(t for t in texts[1:] if t.strip()), wants, tuple(notes))


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    if declared not in TEXT_TYPES and not name.casefold().endswith(TEXT_SUFFIXES):
        return None, f"Skipped {name}: not a text file."
    if data is None:
        return None, f"Skipped {name}: only inline file content is read."
    if len(data) > MAX_FILE_BYTES:
        return None, f"Skipped {name}: larger than 5 MB."
    return f"# file: {PurePath(name).name}\n{data.decode('utf-8', errors='replace')}", None


def from_envelope(text: str) -> str | None:
    """The diff inside another agent's envelope (`items[].diff`, else `summary`), or None."""
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
    items = data.get("items") if isinstance(data.get("items"), list) else []
    diffs = [str(i["diff"]) for i in items if isinstance(i, dict) and isinstance(i.get("diff"), str)]
    if diffs:
        return "\n".join(diffs)
    return str(data["summary"]) if isinstance(data.get("summary"), str) else None


def read_asks(rest: str) -> Asks:
    """Spec and standards: the text after a label (`spec:`, `issue:`, `requirements:`, `acceptance criteria:`,
    `standards:`, `conventions:`) up to the next label. Focus words are read from the whole request."""
    sections: dict[str, list[str]] = {"spec": [], "standards": []}
    current: str | None = None
    for line in (rest or "").split("\n"):
        if _FENCE.match(line):
            continue
        label = _LABEL.search(line)
        if label:
            name = label.group(1).casefold()
            current = "standards" if name in ("standards", "conventions") else "spec"
            line = label.group(2)
        if current:
            sections[current].append(line)
    spec = strip_markers("\n".join(sections["spec"])).strip()
    standards = strip_markers("\n".join(sections["standards"])).strip()
    axis = _ONLY_AXIS.search(rest or "")
    return Asks(spec=spec[:MAX_SPEC_CHARS], standards=standards[:MAX_STANDARDS_CHARS],
                axis=(axis.group(1) or axis.group(2)).casefold() if axis else "",
                nits=bool(_NITS.search(rest or "")), strict=bool(_STRICT.search(rest or "")),
                spec_cut=len(spec) > MAX_SPEC_CHARS, standards_cut=len(standards) > MAX_STANDARDS_CHARS)
