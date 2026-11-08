"""Pure: what the user gave this turn, read from the A2A message parts, and where the draft is.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge: style
  instructions for the model, never part of the draft and never grounding.
- In a session with prior turns the text arrives as "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used (the last draft comes from this agent's own checkpointer).
- A text file part (an API client may send one) is the draft; other file types are named in a note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

import options

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MIN_DRAFT_WORDS = 15
MAX_DRAFT_CHARS = 20_000
FIRST_PART_WORDS = 3_000
CUE_MAX_START = 60
HEAD_MAX_CHARS = 300
REQUEST_MAX_WORDS = 40

TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "application/json"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".json": "application/json"}

CUE = re.compile(r"\b(?:humani[sz](?:e|ing)|de-?ai|polish|remove (?:the |all )?(?:ai|a\.i\.)|make (?:this|it) sound "
                 r"(?:more )?(?:human|natural)|(?:sound|read)s? less like (?:an? )?(?:ai|bot|robot)|less robotic|"
                 r"clean up (?:the )?ai|ai[- ](?:tone|patterns?|writing|feel|sound))", re.IGNORECASE)
_REQUEST_START = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|can you|could you|would you|please|"
                            r"write|translate|summari[sz]e|explain|calculate|compute|tell me|give me|draft|create|"
                            r"generate|make me|list|define)\b", re.IGNORECASE)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_WORD = re.compile(r"\S+")


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_parts(parts: Any) -> Incoming:
    """Splits message parts into the user's text, platform context and text files (duck-typed a2a parts)."""
    texts: list[str] = []
    files: list[tuple[str, str]] = []
    notes: list[str] = []
    for part in parts or ():
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            name = part.filename or "the attached file"
            media = _media_type(name, part.media_type)
            if kind == "raw" and media in TEXT_TYPES:
                files.append((name, part.raw.decode("utf-8", errors="replace")))
            else:
                notes.append(f"I can't read {name} ({media}); paste its text instead.")
    user = current_message(texts[0]) if texts else ""
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), tuple(files), tuple(notes))


def _media_type(name: str, media_type: str | None) -> str:
    declared = (media_type or "").split(";")[0].strip().casefold()
    if declared in TEXT_TYPES:
        return declared
    return TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared or "unknown type")


def word_count(text: str) -> int:
    return len((text or "").split())


def split_draft(text: str) -> tuple[str, str]:
    """(request words, draft). A cue on the first line ("Humanize this:", "Remove the AI tone from this,
    keep my em dashes:") ends at its colon or line break; a short first paragraph before a long rest is the
    request when it only sets options; otherwise the whole message is the draft, unless it reads as a question or a request."""
    stripped = (text or "").strip()
    head, _, rest = stripped.partition("\n")
    cue = CUE.search(head)
    if cue and cue.start() <= CUE_MAX_START and len(head) <= HEAD_MAX_CHARS:
        colon = head.find(":", cue.end())
        if colon != -1:
            return head[:colon].strip(), f"{head[colon + 1:]}\n{rest}".strip()
        return head.strip(), rest.strip()
    match = _PARAGRAPH_BREAK.search(stripped)
    if match and match.start() <= HEAD_MAX_CHARS and _is_option_line(stripped[:match.start()]):
        return stripped[:match.start()].strip(), stripped[match.end():].strip()
    if is_request(stripped):
        return stripped, ""
    return "", stripped


def _is_option_line(head: str) -> bool:
    """A short first paragraph that only sets options ("more casual please"), not a greeting line."""
    parsed = options.parse(head)
    return word_count(head) < MIN_DRAFT_WORDS and (parsed.changed or parsed.as_json)


def is_request(text: str) -> bool:
    plain = (text or "").strip()
    if word_count(plain) >= REQUEST_MAX_WORDS or "\n" in plain:
        return False
    return plain.endswith("?") or bool(_REQUEST_START.match(plain))


def first_part(draft: str, words: int = FIRST_PART_WORDS, limit: int = MAX_DRAFT_CHARS) -> str:
    """About the first `words` words, cut back to a paragraph or line break, never over `limit` chars."""
    ends = [m.end() for m in _WORD.finditer(draft)]
    if len(ends) <= words and len(draft) <= limit:
        return draft
    head = draft[:min(ends[min(words, len(ends)) - 1], limit)]
    for boundary in ("\n\n", "\n", ". "):
        at = head.rfind(boundary)
        if at >= len(head) // 2:
            return head[:at + (1 if boundary == ". " else 0)].rstrip()
    return head.rstrip()
