"""Pure: the request, parsed in code. Launch name, launch date (relative to the turn's clock), scope hints
(`label:`, `project:`, `milestone:`, `doc:`, `marketing project:`), json mode, pasted checklists, and an
envelope from a previous workflow step (its `summary` only)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import dates
import parse_paste
from config import PASTE_LIMIT_CHARS

HINT_KEYS = ("marketing project", "label", "project", "milestone", "doc")
_HINT = re.compile(r"\b(marketing project|label|project|milestone|doc|launch)\s*:\s*\"?([^,;\n\"]+)\"?", re.IGNORECASE)
_JSON_ASK = re.compile(r"\b(?:as|in) json\b|\bjson only\b|^\s*json\s*$", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“”']([^\"“”']{2,80})[\"“”']")
_PREP = r"(?:\s+(?:on|by|for|at|before))?\s*"
_NAME_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bis (?:the )?(?P<n>.+?) (?:ready|good to go|on track|set|launch[- ]ready)\b",
    r"\bgo\s*/?\s*no[- ]?go (?:for|on|call for) (?:the )?(?P<n>.+)",
    r"\b(?:blocking|blocks|blockers? (?:for|on|of)|readiness (?:of|for)|ready to launch) (?:the )?(?P<n>.+)",
    r"\b(?:check|is|are|for) (?:the )?(?P<n>.+?) (?:launch|release|ga)\b",
    r"\b(?:launch|release) (?:of|for) (?:the )?(?P<n>.+)",
))
_TRAILING = re.compile(r"(?:\s+(?:launch|release|ga|ready|to launch|ready to launch|go live))+\s*$", re.IGNORECASE)
_VAGUE = frozenset({"it", "this", "that", "we", "everything", "things", "launch", "release", "the launch", "us",
                    "our launch", "my launch", "again", "it again"})
_MAX_NAME = 80
DOMAIN = re.compile(r"launch|release|ready|readiness|go ?/ ?no[- ]?go|no[- ]go|\bship|block|checklist|\bga\b|"
                    r"go live|\bbeta\b", re.IGNORECASE)
WRITE_ASK = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:close|update|assign|create|mark|edit|change|"
                       r"set|delete|move|reopen|resolve|complete|publish|add)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    launch: str | None = None
    launch_date: date | None = None
    hints: Mapping[str, str] = field(default_factory=dict)
    json_mode: bool = False
    pasted: Mapping[str, str] = field(default_factory=dict)
    from_envelope: bool = False
    truncated: bool = False

    def to_state(self) -> dict:
        return {"launch": self.launch, "launch_date": self.launch_date.isoformat() if self.launch_date else None,
                "hints": dict(self.hints), "json_mode": self.json_mode, "pasted": dict(self.pasted),
                "from_envelope": self.from_envelope, "truncated": self.truncated}

    @staticmethod
    def from_state(data: Mapping[str, Any] | None) -> Request:
        d = dict(data or {})
        when = dates.tool_date(d.get("launch_date")) if d.get("launch_date") else None
        return Request(d.get("launch") or None, when, dict(d.get("hints") or {}), bool(d.get("json_mode")),
                       dict(d.get("pasted") or {}), bool(d.get("from_envelope")), bool(d.get("truncated")))


def wants_json(text: str) -> bool:
    return bool(_JSON_ASK.search(text or ""))


def parse(text: str, today: date, json_metadata: bool = False) -> Request:
    raw = text or ""
    truncated = len(raw) > PASTE_LIMIT_CHARS
    raw = raw[:PASTE_LIMIT_CHARS]
    summary = _envelope_summary(raw)
    head, pasted = parse_paste.split_sections(summary if summary is not None else raw)
    hints, name = _hints(head)
    head, when = _strip(head, today)
    name = name or _name(head) or (_clean(head) if summary is not None else None)
    return Request(name, when, hints, json_metadata or wants_json(raw), pasted, summary is not None, truncated)


def with_answer(request: Request, answer: str, today: date) -> Request:
    """The answer to "Which launch should I check?": the typed name (and a date or hints, if given)."""
    typed = parse(answer, today)
    first = answer.strip().splitlines()[0] if answer.strip() else ""
    name = typed.launch or _clean(_strip(first, today)[0])
    return replace(request, launch=name or None, launch_date=typed.launch_date or request.launch_date,
                   hints={**request.hints, **typed.hints})


def _strip(text: str, today: date) -> tuple[str, date | None]:
    """The text without hints, the launch date phrase and a json ask; and that date."""
    text = _HINT.sub(" ", text)
    when, said = dates.launch_date(text, today)
    if said:
        text = re.sub(_PREP + re.escape(said), " ", text, count=1, flags=re.IGNORECASE)
    return _JSON_ASK.sub(" ", text), when


def _envelope_summary(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    summary = data.get("summary") if isinstance(data, Mapping) else None
    return summary.strip() if isinstance(summary, str) and summary.strip() else None


def _hints(text: str) -> tuple[dict[str, str], str | None]:
    found = {m.group(1).casefold(): m.group(2).strip() for m in _HINT.finditer(text)}
    name = found.pop("launch", None)
    return {k: v for k, v in found.items() if k in HINT_KEYS and v}, name


def _name(text: str) -> str | None:
    quoted = _QUOTED.search(text)
    if quoted:
        return _clean(quoted.group(1))
    first = text.strip().splitlines()[0] if text.strip() else ""
    for pattern in _NAME_PATTERNS:
        match = pattern.search(first)
        if match:
            name = _clean(match["n"])
            if name:
                return name
    return None


def _clean(text: str) -> str | None:
    name = " ".join(text.split()).strip(" ?!.,:;")
    name = _TRAILING.sub("", name).strip(" ?!.,:;")
    name = re.sub(r"^(?:the|our|my)\s+", "", name, flags=re.IGNORECASE)
    if not name or name.casefold() in _VAGUE or len(name) > _MAX_NAME:
        return None
    return name
