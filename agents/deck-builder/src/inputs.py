"""Pure: reading one user message: the request sentence, the pasted notes and data, the slide count,
colours, follow-up commands, and the chunks a long paste is mapped in."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import dataparse
from dataparse import Table

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
DEFAULT_SLIDES = 8
MIN_SLIDES = 3
MAX_SLIDES = 20
CHUNK_CHARS = 12_000
MAX_CHUNKS = 3
MAX_REQUEST_LINE = 400

_MARKERS = re.compile(r"<<<|>>>")
_COUNT = re.compile(r"\b(\d{1,3})\s*-?\s*slides?\b", re.IGNORECASE)
_FOLDER = re.compile(r"\b(?:to|in|into|under)\s+(?:my\s+|the\s+|our\s+)?[\"']?([\w][\w &.-]{0,40}?)[\"']?\s+folder\b",
                     re.IGNORECASE)
_FILE_NAME = re.compile(r"\b(?:call|name)\s+it\s+[\"']?([^\"'\n]{1,80}?)[\"']?\s*(?:[.,;]|$)", re.IGNORECASE)
_UPLOAD = re.compile(r"\bupload\b|\b(?:to|in|into) (?:my |the )?(?:cloud )?drive\b|\bsave it to\b", re.IGNORECASE)
_SCRIPT = re.compile(r"^\s*(?:the\s+)?(?:script|rebuild script|give me the script)\W*$", re.IGNORECASE)
_OUTLINE = re.compile(r"^\s*(?:show|see)\s+(?:me\s+)?(?:the\s+)?outline\W*$", re.IGNORECASE)
_AS_JSON = re.compile(r"\bas json\b", re.IGNORECASE)
_SLIDE_REF = re.compile(r"\bslides?\s+(\d{1,2})((?:\s*(?:,|and|&)\s*\d{1,2})*)", re.IGNORECASE)
_INJECTION = re.compile(r"ignore (?:all |any |the )?(?:previous|prior|above) instructions|system prompt|"
                        r"you are now|disregard (?:the|your) (?:rules|instructions)|add a slide (?:that )?says",
                        re.IGNORECASE)


@dataclass(frozen=True)
class Parsed:
    request: str
    notes: str
    tables: tuple[Table, ...] = ()
    slide_count: int | None = None
    injected: tuple[str, ...] = ()       # note lines that read as instructions: never grounding
    notes_note: str = ""                 # coverage: what part of a long paste was read
    chunks: tuple[str, ...] = field(default_factory=tuple)


def current_message(raw: str) -> str:
    idx = raw.rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def strip_markers(text: str) -> str:
    return _MARKERS.sub("", text)


def parse(text: str, first_table: int = 1) -> Parsed:
    clean = strip_markers(text).strip()
    request, body = _split(clean)
    body = _envelope_outline(body) or body
    notes, tables = dataparse.extract(body, first_table)
    injected = tuple(line.strip() for line in notes.splitlines() if _INJECTION.search(line))
    chunks, note = chunk(notes)
    return Parsed(request=request, notes=notes.strip(), tables=tables, slide_count=slide_count(request),
                  injected=injected, notes_note=note, chunks=chunks)


def _split(text: str) -> tuple[str, str]:
    """The first line is the request when more follows; a one-line message is all request."""
    first, _, rest = text.partition("\n")
    if rest.strip() and len(first) <= MAX_REQUEST_LINE:
        return first.strip(), rest.strip()
    return text, ""


def _envelope_outline(body: str) -> str | None:
    """A previous agent's envelope (`items[]` with title/body) read as an outline."""
    start = body.find("{")
    if start == -1:
        return None
    try:
        data = json.loads(body[start:body.rfind("}") + 1])
    except ValueError:
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return None
    lines = [f"- {i.get('title', '')}: {i.get('body', '')}".strip() for i in items if isinstance(i, dict)]
    return "\n".join(line for line in lines if line != "- :") or None


def slide_count(text: str) -> int | None:
    match = _COUNT.search(text)
    if not match:
        return None
    return min(MAX_SLIDES, max(MIN_SLIDES, int(match.group(1))))


def chunk(notes: str) -> tuple[tuple[str, ...], str]:
    """Chunks for the map step when notes exceed one chunk (else none), and the coverage line."""
    if len(notes) <= CHUNK_CHARS:
        return (), ""
    covered = notes[:CHUNK_CHARS * MAX_CHUNKS]
    chunks = tuple(covered[i:i + CHUNK_CHARS] for i in range(0, len(covered), CHUNK_CHARS))
    if len(notes) > len(covered):
        return chunks, (f"Your notes are {len(notes):,} characters; I read the first {len(covered):,} in "
                        f"{len(chunks)} parts. Paste the rest separately to include it.")
    return chunks, f"Your notes ({len(notes):,} characters) were read in {len(chunks)} parts."


def folder_name(text: str) -> str | None:
    match = _FOLDER.search(text)
    return match.group(1).strip() if match else None


def file_name(text: str) -> str | None:
    match = _FILE_NAME.search(text)
    return match.group(1).strip() if match else None


def wants_upload(text: str) -> bool:
    return bool(_UPLOAD.search(text))


def is_script(text: str) -> bool:
    return bool(_SCRIPT.match(text))


def is_outline(text: str) -> bool:
    return bool(_OUTLINE.match(text))


def wants_json(text: str) -> bool:
    return bool(_AS_JSON.search(text))


def slide_refs(text: str) -> tuple[int, ...]:
    """Slide numbers the instruction names ("slide 3", "slides 2 and 4")."""
    found: list[int] = []
    for match in _SLIDE_REF.finditer(text):
        found.append(int(match.group(1)))
        found.extend(int(n) for n in re.findall(r"\d{1,2}", match.group(2) or ""))
    return tuple(dict.fromkeys(found))


def is_upload_only(text: str) -> bool:
    """"upload it", "upload to my Board folder": an upload with no other edit."""
    stripped = _FOLDER.sub("", text)
    stripped = _FILE_NAME.sub("", stripped)
    words = re.findall(r"[a-z]+", stripped.casefold())
    filler = {"upload", "it", "the", "deck", "to", "my", "drive", "please", "now", "and", "cloud", "this", "save",
              "put", "in", "into", "file", "pptx", "can", "you", "go", "ahead", "ok", "okay", "then"}
    return wants_upload(text) and all(w in filler for w in words)
