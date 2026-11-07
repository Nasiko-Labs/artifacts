"""Pure data: the records, reading the model's JSON answers, and state (de)serialisation. What survives
the code checks is decided in checks.py; nothing here trusts a value."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any, TypeVar

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
BUG_STATUSES = ("ok", "off_topic", "no_tracker_tool", "not_found")
MATCH_KINDS = ("linked", "keyword")

T = TypeVar("T")


@dataclass(frozen=True)
class Bug:
    key: str
    summary: str
    description_excerpt: str = ""
    status: str = ""
    status_changed_at: str = ""
    created_at: str = ""
    fix_versions: tuple[str, ...] = ()
    url: str = ""
    remote_links: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShipEvidence:
    """What the ship step reported for one fix version. The model never says "shipped"."""

    version: str
    tracker_released: bool | None = None
    tracker_release_date: str = ""
    tag_name: str = ""
    tag_url: str = ""
    tag_published_at: str = ""
    is_draft: bool = False
    is_prerelease: bool = False
    compare_status: str | None = None


@dataclass(frozen=True)
class ShipVerdict:
    verdict: str            # shipped | merged_not_released | released_in_tracker_only | unverified
    version: str = ""
    release_date: str = ""  # YYYY-MM-DD (UTC), only when shipped
    reason: str = ""
    tag_url: str = ""

    @property
    def shipped(self) -> bool:
        return self.verdict == "shipped"


@dataclass(frozen=True)
class Ticket:
    id: str
    url: str = ""
    subject: str = ""
    status: str = ""
    created_at: str = ""
    requester_name: str = ""
    organization_id: str = ""
    organization_name: str = ""
    tier_hint: str = ""
    complaint_quote: str = ""
    match: str = "keyword"
    tier: str = "unknown"   # set by code (tiers.py), never by the model


@dataclass(frozen=True)
class BugAnswer:
    status: str
    bugs: tuple[Bug, ...]


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
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _texts(value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, list) else []
    return tuple(t for t in (text_of(v) for v in items) if t)


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    lowered = text_of(value).casefold()
    return True if lowered == "true" else False if lowered == "false" else None


def parse_bugs(text: str) -> BugAnswer | None:
    data = load_json_object(text)
    if data is None or data.get("status") not in BUG_STATUSES or not isinstance(data.get("bugs", []), list):
        return None
    bugs = tuple(b for b in (_bug(raw) for raw in data.get("bugs") or ()) if b is not None)
    return BugAnswer(data["status"], bugs)


def _bug(raw: Any) -> Bug | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("key")):
        return None
    return Bug(key=text_of(raw.get("key")), summary=text_of(raw.get("summary")),
               description_excerpt=text_of(raw.get("description_excerpt")), status=text_of(raw.get("status")),
               status_changed_at=text_of(raw.get("status_changed_at")), created_at=text_of(raw.get("created_at")),
               fix_versions=_texts(raw.get("fix_versions")), url=text_of(raw.get("url")),
               remote_links=_texts(raw.get("remote_links")))


def parse_ship(text: str) -> tuple[ShipEvidence, ...] | None:
    data = load_json_object(text)
    raw = data.get("versions") if data else None
    if not isinstance(raw, list):
        return None
    return tuple(e for e in (_evidence(item) for item in raw) if e is not None)


def _evidence(raw: Any) -> ShipEvidence | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("version")):
        return None
    compare = text_of(raw.get("compare_status")).casefold() or None
    return ShipEvidence(version=text_of(raw.get("version")), tracker_released=_flag(raw.get("tracker_released")),
                        tracker_release_date=text_of(raw.get("tracker_release_date")),
                        tag_name=text_of(raw.get("tag_name")), tag_url=text_of(raw.get("tag_url")),
                        tag_published_at=text_of(raw.get("tag_published_at")),
                        is_draft=_flag(raw.get("is_draft")) is True,
                        is_prerelease=_flag(raw.get("is_prerelease")) is True, compare_status=compare)


def parse_keywords(text: str) -> tuple[str, ...]:
    data = load_json_object(text)
    return _texts(data.get("keywords")) if data else ()


def parse_tickets(text: str) -> tuple[Ticket, ...] | None:
    data = load_json_object(text)
    raw = data.get("tickets") if data else None
    if not isinstance(raw, list):
        return None
    return tuple(t for t in (_ticket(item) for item in raw) if t is not None)


def _ticket(raw: Any) -> Ticket | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("id")):
        return None
    match = text_of(raw.get("match")).casefold()
    names = ("url", "subject", "status", "created_at", "requester_name", "organization_id", "organization_name",
             "tier_hint", "complaint_quote")
    values = {name: text_of(raw.get(name)) for name in names}
    return Ticket(id=text_of(raw.get("id")).lstrip("#"), match=match if match in MATCH_KINDS else "keyword", **values)


def parse_body(text: str) -> str:
    data = load_json_object(text)
    return text_of(data.get("body")) if data else ""


def to_state(items: Sequence[Any]) -> list[dict]:
    return [asdict(item) for item in items]


def from_state(cls: type[T], raw: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None) -> Any:
    """One record from a dict (None from None), or a tuple of records from a list."""
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return _one(cls, raw)
    return tuple(_one(cls, item) for item in raw)


def _one(cls: type[T], raw: Mapping[str, Any]) -> T:
    names = {f.name: f for f in fields(cls)}
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in raw.items() if k in names}
    return cls(**values)
