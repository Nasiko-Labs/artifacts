"""The extract step: one call per window of note entries (one window up to 24,000 chars), validated in code.
Windows run in order until the call budget or the turn deadline runs out; what was covered is reported."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import prompts
from chunking import MAX_WINDOWS, windows
from entries import Entry, render_for_model
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("shift_handoff.extract")

RESERVED_CALLS = 2       # one grounding repair and the compose call
MAX_ITEMS_PER_WINDOW = 60
MAX_INSTRUCTIONS_PER_WINDOW = 30
STATUSES = ("ok", "off_topic", "not_notes")


@dataclass(frozen=True)
class Extraction:
    status: str                               # ok | off_topic | not_notes | failed
    items: tuple[dict, ...] = ()
    instructions: tuple[dict, ...] = ()
    windows: int = 0
    done: int = 0
    covered: tuple[str, ...] = field(default=())   # entry ids sent to the model

    @property
    def closed_early(self) -> bool:
        return self.done < self.windows


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def _item(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    what, snippet = _text(raw.get("what"), 300), _text(raw.get("snippet"), 400)
    entries = [e for e in raw.get("entries") or [] if isinstance(e, str)]
    if not what or not snippet or not entries:
        return None
    return {"what": what, "snippet": snippet, "entries": entries,
            "refs": [r for r in raw.get("refs") or [] if isinstance(r, str) and r.strip()][:5],
            **{k: _text(raw.get(k), 200) for k in ("owner", "customer", "status_words", "next_step", "due")}}


def _instruction(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    text, snippet, entry = _text(raw.get("text"), 300), _text(raw.get("snippet"), 400), _text(raw.get("entry"), 12)
    if not text or not snippet or not entry:
        return None
    kind = "promise" if raw.get("kind") == "promise" else "instruction"
    return {"text": text, "snippet": snippet, "entry": entry, "kind": kind}


def validate(data: Any) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
        return None
    if data["status"] != "ok":
        return {"status": data["status"], "items": [], "instructions": []}
    if not isinstance(data.get("items", []), list) or not isinstance(data.get("instructions", []), list):
        return None
    items = [i for i in map(_item, data.get("items") or []) if i][:MAX_ITEMS_PER_WINDOW]
    found = [i for i in map(_instruction, data.get("instructions") or []) if i][:MAX_INSTRUCTIONS_PER_WINDOW]
    return {"status": "ok", "items": items, "instructions": found}


async def extract(deps: TurnDeps, entries: list[Entry], instruction: str) -> Extraction:
    parts = windows(entries)[:MAX_WINDOWS] if entries else []
    total = len(windows(entries)) if entries else 0
    items: list[dict] = []
    found: list[dict] = []
    covered: list[str] = []
    done = 0
    for index, window in enumerate(parts):
        if (index > 0 and deps.budget.left() <= RESERVED_CALLS) or not deps.can_call():
            break
        try:
            data = await complete_json(deps, prompts.extract_messages(render_for_model(window), instruction),
                                       prompts.EXTRACT_CONTRACT, validate)
        except LlmError as e:
            log.warning("extract window %d failed: %s", index + 1, e)
            if index == 0:
                return Extraction("failed", windows=total)
            break
        if data["status"] != "ok" and len(parts) == 1:
            return Extraction(data["status"], windows=total, done=1)
        items += data["items"]
        found += data["instructions"]
        covered += [e.id for e in window]
        done += 1
    return Extraction("ok", tuple(items), tuple(found), total, done, tuple(covered))


async def extract_note(deps: TurnDeps, entries: list[Entry]) -> Extraction:
    """`add a note`: one call over the new entries only."""
    return await extract(deps, entries, "")
