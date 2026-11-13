"""Pure: read one A2A message into the user's words, the organization instructions, and attached files.

- The user's words are the FIRST plain text part after the session-history prefix: direct chat sends
  "{history}\\n\\nCurrent message: {text}". The history is kept apart (`history`, parsed by history.py):
  it is this stateless agent's only memory of earlier turns.
- Later plain text parts are organization instructions (policy, agent knowledge) the platform
  appends: system context for the prompt, never a section to brief from.
- A text file part (text/plain, text/markdown, text/csv, application/json) is one section, labelled
  with its filename; any other attachment is skipped and named in a note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import history as hist
import sections as sec

MIN_SOURCE_WORDS = 40
MAX_TOTAL_CHARS = 72_000
SINGLE_PASS_CHARS = 30_000
INSTRUCTION_MAX_CHARS = 300
INSTRUCTION_MIN_REST_CHARS = 500
CUE_INSTRUCTION_MAX_WORDS = 25

TEXT_MEDIA_TYPES = frozenset({"text/plain", "text/markdown", "text/csv", "application/json"})
TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".csv", ".json")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
# A short first line that is clearly about the brief ("Add this too:", "Brief for the CRO:").
_INSTRUCTION_CUE = re.compile(
    r"\b(?:brief|add|include|also|new|start over|start fresh|for the|for our|summari[sz]e|exec|headline)\b|:\s*$",
    re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    text: str
    org: tuple[str, ...] = ()
    files: tuple[sec.Section, ...] = ()
    skipped: tuple[str, ...] = ()
    history: tuple[hist.Turn, ...] = ()


def current_message(raw: str) -> str:
    return hist.split(raw)[1]


def words(text: str) -> int:
    return len((text or "").split())


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts: list[str] = []
    files: list[sec.Section] = []
    skipped: list[str] = []
    for part in getattr(message, "parts", None) or ():
        text, raw, url = getattr(part, "text", ""), getattr(part, "raw", b""), getattr(part, "url", "")
        name, media = getattr(part, "filename", "") or "", (getattr(part, "media_type", "") or "").lower()
        if text and not name:
            texts.append(text)
        elif text or raw:
            _read_file(name, media, text or _decode(raw), files, skipped)
        elif url or name:
            skipped.append(name or "a linked file")
    if not texts:
        return Incoming("", (), tuple(files), tuple(skipped))
    block, current = hist.split(texts[0])
    return Incoming(current.strip(), tuple(t.strip() for t in texts[1:] if t.strip()), tuple(files),
                    tuple(skipped), hist.parse(block))


def split_instruction(text: str) -> tuple[str, str]:
    """(instruction, pasted source). A short first paragraph followed by enough material is the
    instruction; a short message overall is only an instruction; anything else is all source."""
    text = (text or "").strip()
    parts = _PARAGRAPH_BREAK.split(text, maxsplit=1)
    if len(parts) == 2:
        first, rest = parts[0].strip(), parts[1].strip()
        if len(first) <= INSTRUCTION_MAX_CHARS and _is_instruction(first, rest):
            return first, rest
    if words(text) < MIN_SOURCE_WORDS:
        return text, ""
    return "", text


def _is_instruction(first: str, rest: str) -> bool:
    if len(rest) >= INSTRUCTION_MIN_REST_CHARS:
        return True
    return (words(first) <= CUE_INSTRUCTION_MAX_WORDS and bool(_INSTRUCTION_CUE.search(first))
            and words(rest) >= MIN_SOURCE_WORDS)


def _read_file(name: str, media: str, text: str, files: list, skipped: list) -> None:
    base_media = media.split(";", 1)[0].strip()
    if base_media in TEXT_MEDIA_TYPES or name.lower().endswith(TEXT_SUFFIXES):
        if text.strip():
            files.append(sec.file_section(name, text))
        return
    skipped.append(name or base_media or "an attachment")


def _decode(raw: bytes) -> str:
    try:
        return bytes(raw).decode("utf-8", errors="replace")
    except (TypeError, ValueError):
        return ""


def skipped_note(skipped: tuple[str, ...]) -> str | None:
    if not skipped:
        return None
    return (f"Skipped {', '.join(skipped)}: I can only brief from pasted text and text, markdown, CSV or "
            "JSON files.")
