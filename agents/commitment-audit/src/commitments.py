"""Pure: the commitment record, reading the commitments step's JSON, and the code post-check.

A commitment survives only when its name, and its link or at least one tracker key, appear in this
turn's tool output. Keys, the reported status text, its link and the owner are each kept only when a
tool returned them verbatim. Duplicates (same first key, else same name) are kept once. At most
MAX_COMMITMENTS are audited; the rest are named as "not audited this run"."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from verbatim import (
    appears_in,
    int_or_none,
    load_json_object,
    normalise_text,
    parse_day,
    text_of,
)

MAX_COMMITMENTS = 15
Status = Literal["ok", "off_topic", "no_source_tool", "need_source"]
_STATUSES = ("ok", "off_topic", "no_source_tool", "need_source")


@dataclass(frozen=True)
class Commitment:
    cid: str
    name: str
    owner: str | None = None
    reported_text: str | None = None
    reported_at: str | None = None
    reported_link: str | None = None
    keys: tuple[str, ...] = ()
    link: str | None = None
    child_count: int | None = None

    def to_dict(self) -> dict:
        return {**asdict(self), "keys": list(self.keys)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Commitment:
        return Commitment(**{**dict(data), "keys": tuple(data.get("keys") or ())})


@dataclass(frozen=True)
class SourceAnswer:
    status: Status
    items: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Checked:
    audited: tuple[Commitment, ...]
    not_audited: tuple[str, ...] = ()
    dropped: int = 0


def parse_source_answer(text: str) -> SourceAnswer | None:
    data = load_json_object(text)
    if data is None or data.get("status") not in _STATUSES:
        return None
    raw = data.get("commitments") or []
    if not isinstance(raw, list):
        return None
    return SourceAnswer(data["status"], tuple(item for item in raw if isinstance(item, Mapping)))


def check(items: Sequence[Mapping[str, Any]], seen_text: str) -> Checked:
    seen = normalise_text(seen_text)
    kept: list[Commitment] = []
    identities: set[str] = set()
    for item in items:
        commitment = _checked(item, seen)
        identity = _identity(commitment) if commitment else ""
        if commitment is None or identity in identities:
            continue
        identities.add(identity)
        kept.append(commitment)
    dropped = len(items) - len(kept)
    ordered = _largest_first(kept) if len(kept) > MAX_COMMITMENTS else kept
    audited = tuple(_with_cid(c, n) for n, c in enumerate(ordered[:MAX_COMMITMENTS], start=1))
    return Checked(audited, tuple(c.name for c in ordered[MAX_COMMITMENTS:]), dropped)


def _checked(item: Mapping[str, Any], seen: str) -> Commitment | None:
    name = text_of(item.get("name"))
    if not appears_in(name, seen):
        return None
    keys = tuple(dict.fromkeys(k for k in _keys(item.get("keys")) if appears_in(k, seen)))
    link = _verbatim(item.get("link"), seen)
    if not keys and link is None:
        return None
    reported = _verbatim(item.get("reported_status_text"), seen)
    reported_at = text_of(item.get("reported_at"))
    return Commitment(
        cid="", name=name, owner=_verbatim(item.get("owner"), seen), reported_text=reported,
        reported_at=reported_at if reported and parse_day(reported_at) else None,
        reported_link=_verbatim(item.get("reported_source_link"), seen) if reported else None,
        keys=keys, link=link, child_count=_count(item.get("child_count")),
    )


def _keys(raw: Any) -> list[str]:
    values = raw if isinstance(raw, list) else [raw]
    return [text_of(v) for v in values if text_of(v)]


def _verbatim(value: Any, seen: str) -> str | None:
    text = text_of(value)
    return text if appears_in(text, seen) else None


def _count(value: Any) -> int | None:
    count = int_or_none(value)
    return count if count is not None and count >= 0 else None


def _identity(commitment: Commitment) -> str:
    return f"key:{commitment.keys[0].casefold()}" if commitment.keys else f"name:{normalise_text(commitment.name)}"


def _largest_first(items: list[Commitment]) -> list[Commitment]:
    """Most child issues first (unknown counts last); the model's order breaks ties."""
    return sorted(items, key=lambda c: -(c.child_count if c.child_count is not None else -1))


def _with_cid(commitment: Commitment, number: int) -> Commitment:
    return Commitment(**{**asdict(commitment), "cid": f"c{number}", "keys": commitment.keys})


def listing(commitments: Sequence[Commitment]) -> list[dict]:
    return [{"cid": c.cid, "name": c.name, "keys": list(c.keys), "link": c.link} for c in commitments]
