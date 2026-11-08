"""Pure: pasted audit events and who changed a drifted resource by hand.

Accepted: JSON (one object, a list, `{"Records": [...]}`, or one object per line) with actor / time /
event / resource fields in the common audit shapes, and text rows `actor | time | event | resource`.
A drift is matched only when one of its identifiers (id, ARN suffix, name) appears in the event, the
event is mutating, and it happened within `WINDOW_DAYS` before now. The latest human event wins;
actors that look like a pipeline are tagged `automation`. No match: "unknown", never a guess.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

WINDOW_DAYS = 14
NO_AUDIT = "unknown (no audit events pasted)"
NO_MATCH = "unknown (no audit event matched)"
_MUTATING = re.compile(r"^(?:put|update|modify|create|delete|authorize|revoke|attach|detach|replace|patch|edit|"
                       r"apply|scale|set)|\b(?:edit|patch|scale|apply|annotate|label|set)\b", re.IGNORECASE)
_AUTOMATION = re.compile(r"terraform|git\w*-actions|pipeline|(?<![a-z])ci(?![a-z])|atlantis|argocd|flux",
                         re.IGNORECASE)
_ACTOR_KEYS = (("userIdentity", "arn"), ("userIdentity", "userName"), ("userIdentity", "principalId"),
               ("user", "username"), ("actor",), ("user",), ("principal",), ("caller",))
_TIME_KEYS = ("eventTime", "time", "timestamp", "requestReceivedTimestamp", "date")
_EVENT_KEYS = ("eventName", "event", "action", "verb", "operation")


@dataclass(frozen=True)
class Event:
    actor: str
    time: datetime
    event: str
    text: str  # everything the event says, for identifier matching

    @property
    def automation(self) -> bool:
        return bool(_AUTOMATION.search(self.actor))

    def to_dict(self) -> dict:
        out = {"actor": self.actor, "time": self.time.strftime("%Y-%m-%d %H:%M UTC"), "event": self.event,
               "source": "audit"}
        return {**out, "automation": True} if self.automation else out


def parse(text: str) -> tuple[Event, ...]:
    body = (text or "").strip()
    if not body:
        return ()
    records = _json_records(body)
    if records is None:
        return tuple(e for e in (_row(line) for line in body.splitlines()) if e)
    return tuple(e for e in (_record(r) for r in records) if e)


def _json_records(body: str) -> list | None:
    try:
        data = json.loads(body)
    except ValueError:
        lines = [ln for ln in body.splitlines() if ln.strip()]
        try:
            data = [json.loads(ln) for ln in lines]
        except ValueError:
            return None
    if isinstance(data, dict):
        data = data.get("Records") or data.get("events") or data.get("items") or [data]
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else None


def _record(record: dict) -> Event | None:
    actor = next((v for keys in _ACTOR_KEYS if isinstance(v := _dig(record, keys), str) and v), "")
    when = next((t for k in _TIME_KEYS if (t := _time(record.get(k)))), None)
    event = next((v for k in _EVENT_KEYS if isinstance(v := record.get(k), str) and v), "")
    if not (actor and when and event):
        return None
    return Event(actor, when, event, json.dumps(record))


def _row(line: str) -> Event | None:
    cells = [c.strip() for c in line.split("|")]
    if len(cells) < 4:
        return None
    when = _time(cells[1])
    if when is None or not cells[0] or not cells[2]:
        return None
    return Event(cells[0], when, cells[2], line)


def _dig(record: dict, keys: tuple[str, ...]) -> Any:
    node: Any = record
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def is_mutating(event: str) -> bool:
    return bool(_MUTATING.search(event))


def changed_by(events: Sequence[Event], identifiers: Sequence[str], now: datetime) -> dict | str:
    """The manual actor for a resource with these identifiers, or an "unknown" string."""
    if not events:
        return NO_AUDIT
    since = now - timedelta(days=WINDOW_DAYS)
    wanted = [i for i in (_suffix(x) for x in identifiers) if len(i) >= 3]
    hits = [e for e in events if since <= e.time <= now and is_mutating(e.event) and _mentions(e.text, wanted)]
    if not hits:
        return NO_MATCH
    humans = [e for e in hits if not e.automation]
    return max(humans or hits, key=lambda e: e.time).to_dict()


def _suffix(identifier: str) -> str:
    """An ARN or path is matched by its last segment (`arn:...:db:orders-prod` → `orders-prod`)."""
    return re.split(r"[/:]", identifier)[-1] if identifier.startswith("arn:") else identifier


def _mentions(text: str, identifiers: Sequence[str]) -> bool:
    return any(re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", text) for i in identifiers)
