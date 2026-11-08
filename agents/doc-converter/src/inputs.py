"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part; with chat history only the text after the last
  "Current message:" marker. Later text parts are org policy / agent knowledge, never the request.
- The first file part (inline bytes, at most 5 MB) is a document to convert; a larger one, a second one
  or a file link is refused with a reason.
- A pasted HTML block (from the first tag) or delimited block (CSV/TSV lines) is split off the request.
- Another step's envelope (JSON with `items[].name` / `items[].link`) names the files.
- Options read in code: `pages 3-7` (PDF) and `sheet "Totals"` (Excel).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

from models import Failure

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_PART_BYTES = 5 * 1024 * 1024
MAX_PASTE_CHARS = 100_000
MAX_FILES = 5

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_HTML_START = re.compile(r"<(?:!doctype|html|body|head|div|p|h[1-6]|table|ul|ol|span|section|article|a|br|tr|td"
                         r"|th|pre|main)\b", re.IGNORECASE)
_PAGES = re.compile(r"\bpages?\s+(\d{1,5})\s*(?:-|–|to)\s*(\d{1,5})\b", re.IGNORECASE)
SHEET_OPTION = re.compile(r"\bsheet\s+[\"“'‘]([^\"”'’]{1,100})[\"”'’]", re.IGNORECASE)


@dataclass(frozen=True)
class FileIn:
    name: str
    media_type: str
    data: bytes


@dataclass(frozen=True)
class Pasted:
    kind: str  # "html" | "csv" | "tsv"
    text: str


@dataclass(frozen=True)
class Incoming:
    request: str
    files: tuple[FileIn, ...] = ()
    pasted: tuple[Pasted, ...] = ()
    refused: tuple[Failure, ...] = ()
    named: tuple[dict, ...] = ()  # from an envelope: [{"query": name, "link": link}]
    wants_json: bool = False
    pages: tuple[int, int] | None = None
    sheet: str | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.files or self.pasted)


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts = [p.text for p in message.parts if p.WhichOneof("content") == "text"]
    files, refused = _file_parts(message.parts)
    raw = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(raw))
    return parse_text(raw, files=files, refused=refused, wants_json=wants)


def parse_text(raw: str, files: tuple[FileIn, ...] = (), refused: tuple[Failure, ...] = (),
               wants_json: bool = False) -> Incoming:
    named = from_envelope(raw)
    if named is not None:
        return Incoming("", files, (), refused, named[:MAX_FILES], wants_json)
    request, pasted, paste_refused = split_pasted(raw)
    pages = _PAGES.search(request)
    sheet = SHEET_OPTION.search(request)
    return Incoming(request.strip(), files, pasted, (*refused, *paste_refused), (), wants_json,
                    (int(pages.group(1)), int(pages.group(2))) if pages else None,
                    sheet.group(1).strip() if sheet else None)


def _file_parts(parts: Any) -> tuple[tuple[FileIn, ...], tuple[Failure, ...]]:
    files: list[FileIn] = []
    refused: list[Failure] = []
    for part in parts:
        kind = part.WhichOneof("content")
        if kind not in ("raw", "url"):
            continue
        name = part.filename or "attached file"
        if kind == "url":
            refused.append(Failure(name=name, reason="file links in a message are not fetched; send the bytes"))
        elif files:
            refused.append(Failure(name=name, reason="one file per message through the API; send it separately"))
        elif len(part.raw) > MAX_FILE_PART_BYTES:
            mb = len(part.raw) / (1024 * 1024)
            refused.append(Failure(name=name, reason=f"is {mb:.1f} MB; files sent in a message are limited to 5 MB"))
        else:
            files.append(FileIn(name=name, media_type=part.media_type or "", data=bytes(part.raw)))
    return tuple(files), tuple(refused)


def split_pasted(raw: str) -> tuple[str, tuple[Pasted, ...], tuple[Failure, ...]]:
    """(request sentence, pasted block, refusals). One block per message: HTML from its first tag, or a
    run of at least two delimited lines at the end of the message."""
    html = _HTML_START.search(raw or "")
    if html and re.search(r"</[a-zA-Z]|/>|<br\b", raw[html.start():]):
        return _block(raw[:html.start()], raw[html.start():], "html")
    lines = (raw or "").splitlines()
    start = _delimited_start(lines)
    if start is None:
        return raw or "", (), ()
    block = "\n".join(lines[start:]).strip()
    return _block("\n".join(lines[:start]), block, "tsv" if "\t" in lines[start] else "csv")


def _block(request: str, text: str, kind: str) -> tuple[str, tuple[Pasted, ...], tuple[Failure, ...]]:
    if len(text) > MAX_PASTE_CHARS:
        reason = f"is {len(text):,} characters; a paste is limited to {MAX_PASTE_CHARS:,}"
        return request, (), (Failure(name=f"pasted {kind.upper()}", reason=reason),)
    return request, (Pasted(kind, text),), ()


def _delimited_start(lines: list[str]) -> int | None:
    """The first line of a trailing block of >= 2 lines that share a tab or comma count >= 1."""
    body = [i for i, line in enumerate(lines) if line.strip()]
    if len(body) < 2:
        return None
    for sep in ("\t", ","):
        count = lines[body[-1]].count(sep)
        if count < 1:
            continue
        start = body[-1]
        for i in reversed(body[:-1]):
            if lines[i].count(sep) != count:
                break
            start = i
        if body.index(start) <= len(body) - 2:
            return start
    return None


def from_envelope(text: str) -> tuple[dict, ...] | None:
    """File names/links from another step's envelope, or None when `text` is not one."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    named = tuple({"query": _str(i.get("name")), "link": _str(i.get("link"))} for i in items
                  if isinstance(i, dict) and (_str(i.get("name")) or _str(i.get("link"))))
    return named or None


def strip_json_words(text: str) -> str:
    return _JSON_WORDS.sub(" ", text or "").strip()


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
