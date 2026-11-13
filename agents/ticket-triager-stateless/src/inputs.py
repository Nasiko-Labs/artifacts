"""Pure: what the user gave this turn, and the optional instruction/config lines.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  system instructions for the signals step, never tickets and never the request.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}". The text after the last marker
  is this turn; the history before it is kept as turns (`history.py`) and is the agent's only memory: a
  follow-up re-reads the earlier batch and triage from it (`recall.py`).
- A text file part (csv/json/plain/markdown) is appended to the pasted text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

import history
from history import Turn
from model import CATEGORIES

FILE_TYPES = frozenset({"text/plain", "text/markdown", "text/csv", "text/tab-separated-values", "application/json"})
MAX_FILE_BYTES = 5 * 1024 * 1024
OWNER_MAX_CHARS = 60

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_SLA = re.compile(r"^\s*sla\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_SLA_ITEM = re.compile(r"\b(P[1-4])\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hrs?|hours?|d|days?)\b",
                       re.IGNORECASE)
_ROUTING = re.compile(r"^\s*routing\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_ROUTE_ITEM = re.compile(r"\s*([a-z _-]+?)\s*(?:->|→|=>|:|=)\s*([^;,\n]+)", re.IGNORECASE)
_ONLY_OPEN = re.compile(r"\bonly open\b|\bopen (?:tickets )?only\b", re.IGNORECASE)
_TOP = re.compile(r"\btop (\d{1,3})\b", re.IGNORECASE)
_UNIT_HOURS = {"m": 1 / 60, "h": 1.0, "d": 24.0}
_CATEGORY_ALIASES = {"how to": "how_to", "how-to": "how_to", "howto": "how_to", "access": "account_access",
                     "account access": "account_access", "login": "account_access", "data": "data_issue",
                     "data issue": "data_issue", "feature": "feature_request", "feature request": "feature_request",
                     "cancel": "cancellation", "churn": "cancellation", "account_access": "account_access"}


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, platform policy parts, whether JSON output was asked, and the
    earlier turns the platform sent with it (oldest first)."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    metadata_json: bool = False
    history: tuple[Turn, ...] = ()


@dataclass(frozen=True)
class Config:
    sla_hours: tuple[tuple[str, float], ...] = ()
    routing: tuple[tuple[str, str], ...] = ()
    only_open: bool = False
    top: int | None = None


def read_message(message: Any) -> Incoming:
    """The first text part is the user's; later text parts are policy; metadata may ask for JSON."""
    if message is None:
        return Incoming("")
    texts, files = [], []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind == "raw" and _readable(part):
            files.append(part.raw.decode("utf-8", errors="replace"))
    turns, user = history.split(texts[0] if texts else "")
    if files:
        user = "\n\n".join(p for p in (user.strip(), *files) if p.strip())
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    meta_json = str(metadata.get("output") or "").casefold() == "json"
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), meta_json or wants_json(user), meta_json, turns)


def from_text(raw: str, policy: tuple[str, ...] = (), json_metadata: bool = False) -> Incoming:
    """A text-only message (tests and simple callers): the same reading as `read_message`."""
    turns, user = history.split(raw)
    return Incoming(user, tuple(t for t in policy if t.strip()), json_metadata or wants_json(user), json_metadata,
                    turns)


def _readable(part: Any) -> bool:
    media = (part.media_type or "").split(";")[0].strip().casefold()
    name = (part.filename or "").casefold()
    by_name = name.endswith((".csv", ".tsv", ".json", ".txt", ".md"))
    return (media in FILE_TYPES or by_name) and len(part.raw) <= MAX_FILE_BYTES


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))


def read_config(instruction: str) -> Config:
    """SLA / Routing / only open / top N, from the instruction paragraph only (never from ticket text)."""
    sla = ()
    sla_line = _SLA.search(instruction or "")
    if sla_line:
        sla = tuple((p.upper(), float(n) * _UNIT_HOURS[u[0].casefold()]) for p, n, u in _SLA_ITEM.findall(sla_line.group(1)))
    routing = ()
    routing_line = _ROUTING.search(instruction or "")
    if routing_line:
        routing = tuple(pair for pair in (_route(k, v) for k, v in _ROUTE_ITEM.findall(routing_line.group(1))) if pair)
    top = _TOP.search(instruction or "")
    return Config(sla, routing, bool(_ONLY_OPEN.search(instruction or "")), int(top.group(1)) if top else None)


def _route(key: str, owner: str) -> tuple[str, str] | None:
    name = " ".join(key.casefold().split())
    category = _CATEGORY_ALIASES.get(name, name.replace(" ", "_"))
    clean = owner.strip()[:OWNER_MAX_CHARS]
    return (category, clean) if category in CATEGORIES and clean else None


def is_config_line(line: str) -> bool:
    return bool(_SLA.match(line) or _ROUTING.match(line))
