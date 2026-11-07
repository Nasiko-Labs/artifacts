"""Pure data: the promise record, reading the model's JSON answers, state (de)serialisation.
(The record is trimmed from the promise-tracker agent's: no link, no thread reference.)"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

NOT_STATED = "Not stated"
EXTRACT_STATUSES = ("ok", "off_topic")
STATES = ("open", "done", "unclear")


@dataclass(frozen=True)
class Promise:
    """One checked commitment. `due` is YYYY-MM-DD computed in code, or None. `basis` names what a relative
    due phrase was resolved against: "sent" (the message's date), "as_of" (an explicit as-of), "clock"."""

    message_id: str
    to_whom: str
    what: str
    source_quote: str
    due_phrase: str = ""
    due: str | None = None
    sent_on: str = ""
    state: str = "open"
    evidence_quote: str = ""
    basis: str = ""
    order: int = 0           # position in the conversation's pastes (oldest first), for stable sorting


def text_of(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def parse_extract(data: Any) -> dict | None:
    """{"status", "promises": [raw dicts]}, or None when the answer does not match the contract."""
    if not isinstance(data, Mapping) or data.get("status") not in EXTRACT_STATUSES:
        return None
    raw = data.get("promises") or []
    if not isinstance(raw, list) or not all(isinstance(p, Mapping) for p in raw):
        return None
    return {"status": data["status"], "promises": [dict(p) for p in raw]}


def parse_states(data: Any) -> dict | None:
    """{"results": {id: (state, evidence_quote)}}; unreadable entries are skipped (that promise stays open)."""
    if not isinstance(data, Mapping) or not isinstance(data.get("results"), list):
        return None
    results = {}
    for entry in data["results"]:
        if isinstance(entry, Mapping) and entry.get("state") in STATES:
            results[text_of(entry.get("id"))] = (entry["state"], text_of(entry.get("evidence_quote")))
    return {"results": results}


def to_state(items: Sequence[Promise]) -> list[dict]:
    return [asdict(item) for item in items]


def from_state(raw: Sequence[Mapping[str, Any]] | None) -> tuple[Promise, ...]:
    names = {f.name for f in fields(Promise)}
    return tuple(Promise(**{k: v for k, v in dict(item).items() if k in names}) for item in raw or ())
