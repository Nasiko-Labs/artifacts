"""Pure: what the user gave this turn, read from the A2A message, and how it splits into diff, context,
template and style words.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never the request and never evidence.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; the text after the last marker
  is this turn's message and the history before it is parsed into turns (`history.py`): this agent's only
  memory, since nothing is stored between messages.
- An A2A file part (inline text, <= 5 MB) is read as pasted text under a `# file:` label.
- Another agent's envelope (JSON with `items[].diff` or `summary`) is accepted as the diff.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

import diff_parse
import history
from diff_parse import ParsedDiff
from history import Turn

MAX_INPUT_CHARS = 100_000
MAX_FILE_BYTES = 5 * 1024 * 1024
FILE_TYPES = ("text/plain", "text/markdown", "application/json", "text/csv", "text/x-diff", "text/x-patch",
              "application/x-diff", "application/x-patch", "text/x-markdown")
FILE_LABEL = "# file: "

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_MARKERS = re.compile(r"<<<\s*/?[A-Z_]*\s*>>>|<<<|>>>")
_TEMPLATE_MARKER = re.compile(r"^\s*(?:.*\b(?:use|follow|with) (?:this|our|the|my) (?:pr )?template\b[^:\n]*|"
                              r"(?:pr )?template)\s*:\s*(.*)$", re.IGNORECASE)
_TEMPLATE_FENCE = re.compile(r"```(?:markdown|md|text)?[ \t]*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
_DIFF_START = re.compile(r"^(?:diff --git |--- |@@ |\s*\S.*\s\|\s+\d+ [+\-]+$)")
_REQUEST = re.compile(
    r"(?:please\s+)?(?:can you\s+|could you\s+)?(?:write|draft|generate|create|give me|make|produce|prepare|need)?"
    r"\s*(?:me\s+)?(?:a|an|the)?\s*\b(?:pr|pull request)\b\s*(?:title\s*(?:and|&)\s*)?"
    r"(?:description|desc|body|write-?up|summary|draft)?\s*,?\s*(?:for|from|of)?\s*(?:this|the|my|our|these)?\s*"
    r"(?:diff|patch|changes?)?|(?:use|follow|with) (?:this|our|the|my) (?:pr )?template|as json|json only|"
    r"\bconventional commits?\b|\bplain title\b|\bno emojis?\b|\bshort(?:er)?\b|\bhere(?:'s| is) (?:the|my) diff\b",
    re.IGNORECASE)
_PLAIN_TITLE = re.compile(r"\bplain title\b|\bno conventional commits?\b|\bwithout conventional\b|"
                          r"\bnot conventional\b", re.IGNORECASE)
_SHORT = re.compile(r"\bshort(?:er)?\b|\bbrief(?:er)?\b|\bconcise\b|\btighter\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    history: tuple[Turn, ...] = ()   # the platform's earlier turns, oldest first: the only memory


@dataclass(frozen=True)
class Style:
    conventional: bool = True
    short: bool = False

    def to_dict(self) -> dict:
        return {"conventional": self.conventional, "short": self.short}

    @staticmethod
    def from_dict(data: dict | None) -> Style:
        data = data or {}
        return Style(bool(data.get("conventional", True)), bool(data.get("short", False)))


@dataclass(frozen=True)
class Split:
    parsed: ParsedDiff
    context: str = ""
    template: str = ""
    instruction: str = ""


def strip_markers(text: str) -> str:
    return _MARKERS.sub("", text or "")


def read_message(message: Any) -> Incoming:
    """The first text part (plus any file parts) is the user's; later text parts are policy."""
    if message is None:
        return Incoming("")
    texts = [p.text for p in message.parts if p.WhichOneof("content") == "text"]
    block, user = history.split(texts[0]) if texts else ("", "")
    for part in message.parts:
        if part.WhichOneof("content") == "raw" and _readable(part):
            user += f"\n\n{FILE_LABEL}{part.filename or 'attachment'}\n{part.raw.decode('utf-8', 'replace')}"
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants, history.parse(block))


def _readable(part: Any) -> bool:
    media = (part.media_type or "text/plain").split(";")[0].strip().casefold()
    return len(part.raw) <= MAX_FILE_BYTES and (media in FILE_TYPES or media.startswith("text/"))


def from_envelope(text: str) -> str | None:
    """The diff text inside another step's envelope, or None when `text` is not one."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.search(stripped)
    body = fenced.group(1) if fenced else stripped
    start = body.find("{")
    before = body[:start]
    if start == -1 or before.strip() and "\n" not in before or "diff --git" in before or "\n@@ " in before:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(body[start:])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    diffs = [i["diff"] for i in data.get("items") or () if isinstance(i, dict) and isinstance(i.get("diff"), str)]
    if diffs:
        return "\n".join(diffs)
    summary = data.get("summary")
    return summary if isinstance(summary, str) and summary.strip() else None


def style_of(text: str, base: Style | None = None) -> Style:
    base = base or Style()
    conventional = False if _PLAIN_TITLE.search(text or "") else (
        True if re.search(r"\bconventional commits?\b", text or "", re.IGNORECASE) else base.conventional)
    return Style(conventional, bool(_SHORT.search(text or "")) or base.short)


def _take_template(text: str) -> tuple[str, str]:
    """(template, the rest): a fenced markdown block with headings, or the block after a `template:` line."""
    for match in _TEMPLATE_FENCE.finditer(text):
        if _HEADING.search(match.group(1)):
            return match.group(1).strip("\n"), text[:match.start()] + text[match.end():]
    lines = text.split("\n")
    for n, line in enumerate(lines):
        marker = _TEMPLATE_MARKER.match(line)
        if not marker:
            continue
        end = next((j for j in range(n + 1, len(lines)) if _DIFF_START.match(lines[j])), len(lines))
        body = ([marker.group(1)] if marker.group(1).strip() else []) + lines[n + 1:end]
        if _HEADING.search("\n".join(body)):
            return "\n".join(body).strip("\n"), "\n".join(lines[:n] + lines[end:])
    return "", text


def split_input(text: str) -> Split:
    """Diff, context (the user's own words about why), template and the request line."""
    clean = strip_markers(text)
    enveloped = from_envelope(clean)
    if enveloped is not None:
        return Split(diff_parse.parse(enveloped))
    template, rest = _take_template(clean)
    parsed = diff_parse.parse(rest)
    context_lines, instruction = [], []
    for line in parsed.outside:
        if line.startswith(FILE_LABEL):
            continue
        leftover = _REQUEST.sub(" ", line)
        if len(re.findall(r"\w", leftover)) >= 3 and re.search(r"[A-Za-z0-9]{2,}", leftover):
            context_lines.append(line.strip() if not _REQUEST.search(line) else _tidy(leftover))
        elif line.strip():
            instruction.append(line.strip())
    context = "\n".join(context_lines).strip()
    return Split(parsed, context, template, " ".join(instruction))


def _tidy(text: str) -> str:
    return re.sub(r"^[\s.,;:!-]+", "", re.sub(r"\s+", " ", text)).strip()
