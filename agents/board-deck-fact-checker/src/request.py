"""Pure: read the user's message in code. Finds pasted deck text, a pasted `Source:` block of
`metric, period, value` rows, links, a slide range, strict/loose wording, json mode, an envelope of
`claims[]` from an earlier run, and a request to change something (declined: read-only)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import figures
import slides

MAX_MESSAGE_CHARS = 100_000
MIN_PASTED_DECK_CHARS = 120

_URL = re.compile(r"https?://[^\s<>()\"']+")
_JSON_MODE = re.compile(r"\b(?:as json|json only|in json|json output)\b", re.IGNORECASE)
_STRICT = re.compile(r"\bstrict(?:ly)?\b|\bexact(?:ly)?\b|\bno rounding\b", re.IGNORECASE)
_LOOSE = re.compile(r"\bloose(?:ly)?\b|\blenient\b|\bwithin 1 ?%", re.IGNORECASE)
_SOURCE_LINE = re.compile(r"^\s*(?:source|sources|source data|source numbers|source rows)\s*:\s*(.*)$",
                          re.IGNORECASE)
_DECK_LINE = re.compile(r"^\s*(?:deck|deck text|slides)\s*:\s*(.*)$", re.IGNORECASE)
_SEPARATORS = re.compile(r"\s*[|\t;]\s*|,\s+")
_WRITE = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:fix|update|change|edit|correct|rewrite|replace|overwrite|"
    r"write|set|modify|amend|put)\b|\b(?:fix|correct|update|change|edit) (?:the |all |these |those )?"
    r"(?:numbers|figures|values|cells?|slides?|deck|sheet)\b", re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class SourceRow:
    line: int
    metric: str
    period: str
    value: str


@dataclass(frozen=True)
class Request:
    message: str
    mode: str = "default"
    json_mode: bool = False
    deck_text: str = ""
    source_rows: tuple[SourceRow, ...] = ()
    urls: tuple[str, ...] = ()
    slide_range: tuple[int, int] | None = None
    claims: tuple[Mapping[str, Any], ...] = ()
    wants_write: bool = False

    @property
    def sheet_urls(self) -> tuple[str, ...]:
        return tuple(u for u in self.urls if "/spreadsheets/" in u)

    @property
    def deck_urls(self) -> tuple[str, ...]:
        return tuple(u for u in self.urls if "/spreadsheets/" not in u)


def parse(message: str, metadata: Mapping[str, Any] | None = None) -> Request:
    text = (message or "")[:MAX_MESSAGE_CHARS]
    envelope = _envelope_claims(text)
    lines = text.split("\n")
    start, rows = _source_block(lines)
    rest = "\n".join(lines[:start]) if rows else text
    output = str((metadata or {}).get("output") or "").casefold()
    head = rest[:600]
    return Request(
        message=text, mode=mode_of(head), json_mode=output == "json" or bool(_JSON_MODE.search(head)),
        deck_text="" if envelope else _deck_text(rest), source_rows=rows,
        urls=tuple(dict.fromkeys(u.rstrip(".,;") for u in _URL.findall(text))),
        slide_range=slides.slide_range(head), claims=envelope, wants_write=wants_write(head),
    )


def mode_of(text: str) -> str:
    if _STRICT.search(text or ""):
        return "strict"
    return "loose" if _LOOSE.search(text or "") else "default"


def wants_write(text: str) -> bool:
    return bool(_WRITE.search(text or ""))


def _row(line: str, number: int) -> SourceRow | None:
    fields = [f.strip() for f in _SEPARATORS.split(line.strip()) if f.strip()]
    if len(fields) < 3 and line.count(",") == 2:
        fields = [f.strip() for f in line.split(",")]
    if len(fields) < 3 or figures.parse(fields[-1], whole=True) is None:
        return None
    return SourceRow(number, fields[0], " ".join(fields[1:-1]), fields[-1])


def _source_block(lines: list[str]) -> tuple[int, tuple[SourceRow, ...]]:
    """The last `Source:` line followed by rows, and its rows; (len, ()) when there is none."""
    for start in range(len(lines) - 1, -1, -1):
        if not _SOURCE_LINE.match(lines[start]):
            continue
        rows: list[SourceRow] = []
        for number, line in enumerate(lines[start + 1:], 1):
            row = _row(line, number)
            if row is not None:
                rows.append(row)
            elif rows and line.strip():
                break
        if rows:
            return start, tuple(rows)
    return len(lines), ()


def _deck_text(text: str) -> str:
    lines = text.split("\n")
    marked = next((i for i, line in enumerate(lines) if _DECK_LINE.match(line)), None)
    if marked is not None:
        inline = _DECK_LINE.match(lines[marked]).group(1)
        return "\n".join([inline, *lines[marked + 1:]]).strip()
    body = text.strip()
    has_slides = bool(re.search(r"^[ \t]*(?:slide|page)\s*#?\s*\d", body, re.IGNORECASE | re.MULTILINE))
    looks_pasted = len(body) >= MIN_PASTED_DECK_CHARS and "\n" in body and re.search(r"\d", body)
    return body if has_slides or looks_pasted else ""


def _envelope_claims(text: str) -> tuple[Mapping[str, Any], ...]:
    body = _FENCE.sub("", text.strip())
    if not body.startswith("{"):
        return ()
    try:
        data = json.loads(body)
    except ValueError:
        return ()
    claims = data.get("claims") if isinstance(data, Mapping) else None
    return tuple(c for c in claims if isinstance(c, Mapping)) if isinstance(claims, list) else ()
