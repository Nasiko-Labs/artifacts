"""Pure: what the user asked for, parsed in code. The spec reference (a link, or the rest of the message for
the document search), a pasted spec after "Spec:", team and project names, JSON output mode, and an envelope
from a previous workflow step (its `summary` is read as the spec text)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

_SPEC_MARKER = re.compile(r"(?:^|\n|\s)spec\s*:\s*", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")
_TEAM = re.compile(r"\bteam\s*[:=]?\s*\"?([A-Za-z0-9][\w.\- ]{0,40}?)\"?(?=$|[,.;\n]|\s+(?:and|project|with|into)\b)",
                   re.IGNORECASE)
_FOR = re.compile(r"\bfor\s+(?:the\s+)?([A-Z][\w\-]*(?:\s+[A-Z][\w\-]*)?)(?:\s+team)?\b")
_PROJECT = re.compile(r"\bproject\s*[:=]\s*\"?([^\",;\n]{1,80})\"?", re.IGNORECASE)
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
MIN_PASTE_CHARS = 40


@dataclass(frozen=True)
class Ask:
    text: str                       # the user's words (the pasted spec removed)
    pasted: str = ""                # spec text pasted in the message, or an envelope's summary
    pasted_source: str = ""         # "pasted" | "envelope"
    link: str = ""
    team_hints: tuple[str, ...] = ()
    project: str = ""
    wants_json: bool = False

    @property
    def has_spec(self) -> bool:
        return bool(self.pasted) or bool(self.link) or bool(self.text.strip())


def read(message: str, metadata: Mapping | None = None) -> Ask:
    wants_json = bool(_JSON_WORDS.search(message or "")) or (metadata or {}).get("output") == "json"
    envelope = _envelope(message)
    if envelope is not None:
        return Ask(text="", pasted=envelope, pasted_source="envelope", wants_json=wants_json)
    head, pasted = _split_paste(message or "")
    return Ask(text=head.strip(), pasted=pasted, pasted_source="pasted" if pasted else "",
               link=next(iter(_URL.findall(head)), ""), team_hints=team_hints(head), project=_project(head),
               wants_json=wants_json)


def team_hints(text: str) -> tuple[str, ...]:
    """Names that may be the team, in order: after "team", then capitalised words after "for"."""
    found = [m.group(1).strip() for m in _TEAM.finditer(text)] + [m.group(1).strip() for m in _FOR.finditer(text)]
    return tuple(dict.fromkeys(h for h in found if h))


def _project(text: str) -> str:
    match = _PROJECT.search(text)
    return match.group(1).strip() if match else ""


def _split_paste(message: str) -> tuple[str, str]:
    match = _SPEC_MARKER.search(message)
    if match is None:
        return message, ""
    pasted = message[match.end():].strip()
    if len(pasted) < MIN_PASTE_CHARS:
        return message, ""
    return message[:match.start()], pasted


def _envelope(message: str) -> str | None:
    text = (message or "").strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or "summary" not in data or not str(data.get("summary") or "").strip():
        return None
    return str(data["summary"]).strip()
