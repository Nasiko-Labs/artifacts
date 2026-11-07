"""Pure data: the frozen records every step passes around, and their JSON-safe (de)serialisation for
the checkpointer and the A2A task metadata."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

ItemType = Literal["feature_request", "objection", "competitor", "churn_signal"]
ITEM_TYPES: tuple[str, ...] = ("feature_request", "objection", "competitor", "churn_signal")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class Participant:
    name: str
    email: str
    external: bool
    speaker_id: str = ""


@dataclass(frozen=True)
class Call:
    id: str
    url: str
    title: str
    started_at: str
    duration_s: float | None
    participants: tuple[Participant, ...]

    @property
    def external_domains(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(p.email.rsplit("@", 1)[1].casefold() for p in self.participants
                                   if p.external and "@" in p.email))


@dataclass(frozen=True)
class Account:
    company_id: str
    name: str
    domain: str
    url: str
    arr: float | None
    stage: str
    renewal_date: str | None  # ISO date, or None when unknown


@dataclass(frozen=True)
class Turn:
    speaker: str
    external: bool
    offset_s: int
    text: str


@dataclass(frozen=True)
class Item:
    call_id: str
    account_id: str | None
    type: str
    quote: str
    speaker: str
    offset_s: int
    label: str
    competitor: str | None = None


@dataclass(frozen=True)
class NoteDraft:
    company_id: str
    name: str
    url: str
    text: str


def to_dict(record: Any) -> dict:
    return asdict(record)


def call_from(data: Mapping[str, Any]) -> Call:
    people = tuple(Participant(**{f.name: data_p.get(f.name, "" if f.name != "external" else False)
                                  for f in fields(Participant)}) for data_p in data.get("participants") or ())
    return Call(id=str(data["id"]), url=str(data.get("url") or ""), title=str(data.get("title") or ""),
                started_at=str(data.get("started_at") or ""), duration_s=data.get("duration_s"),
                participants=people)


def _plain(cls: type, data: Mapping[str, Any]) -> Any:
    return cls(**{f.name: data.get(f.name) for f in fields(cls)})


def account_from(data: Mapping[str, Any]) -> Account:
    return _plain(Account, data)


def item_from(data: Mapping[str, Any]) -> Item:
    item = _plain(Item, data)
    return Item(**{**asdict(item), "offset_s": int(item.offset_s or 0)})


def note_from(data: Mapping[str, Any]) -> NoteDraft:
    return NoteDraft(**{f.name: str(data.get(f.name) or "") for f in fields(NoteDraft)})


def dump_all(records: Iterable[Any]) -> list[dict]:
    return [to_dict(r) for r in records]


def load_json_object(text: str) -> dict | None:
    """The first-to-last-brace JSON object in a model reply, code fences tolerated."""
    body = _CODE_FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def text_of(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value).strip())


def mm_ss(offset_s: int) -> str:
    minutes, seconds = divmod(max(0, int(offset_s)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
