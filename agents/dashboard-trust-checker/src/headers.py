"""Pure: map the inventory's column headers to canonical fields by synonym, in code. Headers the synonyms do
not know may be mapped by one model call (graph.py) and are checked here; two columns claiming one field are
an ambiguity the user resolves with a single-select question."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

FIELDS = ("name", "owner", "last_refresh", "viewers", "status", "error", "source", "cadence", "last_viewed")
# A model-proposed mapping may target these (an "error" column is found by synonym only).
MODEL_FIELDS = frozenset(FIELDS) - {"error"}
JUDGING_FIELDS = ("last_refresh", "viewers", "status", "error")
_KEY_FIELDS = ("name", "last_refresh", "viewers", "status")
_LABEL = {"name": "name", "owner": "owner", "last_refresh": "last refresh date", "viewers": "viewer count",
          "status": "status", "error": "error text", "source": "source", "cadence": "refresh cadence",
          "last_viewed": "last viewed date"}

SYNONYMS: Mapping[str, tuple[str, ...]] = {
    "name": ("name", "dashboard", "dashboard_name", "title", "dashboard_title", "report", "report_name"),
    "owner": ("owner", "creator", "author", "created_by", "owned_by", "owner_email"),
    "last_refresh": ("last_refresh", "refreshed", "last_refreshed", "refreshed_at", "last_updated", "updated_at",
                     "data_as_of", "as_of", "last_run", "last_refresh_date", "refresh_date"),
    "viewers": ("viewers", "views_30d", "unique_viewers", "views", "viewers_30d", "view_count", "views_last_30_days",
                "unique_viewers_30d", "users_30d"),
    "status": ("status", "health", "query_status", "state", "refresh_status"),
    "error": ("error", "errors", "last_error", "error_message", "query_error"),
    "source": ("source", "tables", "model", "data_source", "dataset"),
    "cadence": ("expected_cadence", "refresh_cadence", "sla", "cadence", "schedule", "refresh_schedule",
                "refresh_frequency", "frequency"),
    "last_viewed": ("last_viewed", "last_viewed_at", "last_opened", "last_accessed", "last_view"),
}
_BY_NAME = {name: f for f, names in SYNONYMS.items() for name in names}
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(header: str) -> str:
    return _NON_ALNUM.sub("_", (header or "").casefold()).strip("_")


@dataclass(frozen=True)
class HeaderMap:
    header: tuple[str, ...]
    fields: Mapping[str, int] = field(default_factory=dict)            # field -> column index
    ambiguous: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    by_model: bool = False

    @property
    def unmapped(self) -> tuple[int, ...]:
        used = set(self.fields.values()) | {i for idx in self.ambiguous.values() for i in idx}
        return tuple(i for i in range(len(self.header)) if i not in used)

    def needs_model(self) -> bool:
        """A key field is missing and some header is not understood."""
        return bool(self.unmapped) and any(f not in self.fields and f not in self.ambiguous for f in _KEY_FIELDS)

    def can_judge(self) -> bool:
        return any(f in self.fields for f in JUDGING_FIELDS)

    def named(self) -> dict[str, str]:
        """field -> the header it maps to (for the envelope)."""
        return {f: self.header[i] for f, i in self.fields.items()}

    def to_dict(self) -> dict:
        return {"header": list(self.header), "fields": dict(self.fields),
                "ambiguous": {f: list(v) for f, v in self.ambiguous.items()}, "by_model": self.by_model}

    @staticmethod
    def from_dict(data: Mapping) -> HeaderMap:
        return HeaderMap(tuple(data.get("header") or ()), {str(k): int(v) for k, v in (data.get("fields") or {}).items()},
                         {str(k): tuple(int(i) for i in v) for k, v in (data.get("ambiguous") or {}).items()},
                         bool(data.get("by_model")))


def map_headers(header: Sequence[str]) -> HeaderMap:
    claims: dict[str, list[int]] = {}
    for i, name in enumerate(header):
        found = _BY_NAME.get(normalize(name))
        if found:
            claims = {**claims, found: [*claims.get(found, []), i]}
    return _settle(tuple(header), claims)


def with_model(current: HeaderMap, proposal: Mapping[str, str]) -> HeaderMap:
    """Add the model's mapping for headers code did not understand. A proposal naming an unknown header, an
    unknown field, or a field already mapped is ignored."""
    unmapped = {current.header[i]: i for i in current.unmapped}
    claims: dict[str, list[int]] = {}
    for raw_header, raw_field in (proposal or {}).items():
        index, target = unmapped.get(str(raw_header)), str(raw_field or "").strip()
        if index is None or target not in MODEL_FIELDS or target in current.fields or target in current.ambiguous:
            continue
        claims = {**claims, target: [*claims.get(target, []), index]}
    if not claims:
        return current
    added = _settle(current.header, claims)
    return replace(current, fields={**current.fields, **added.fields}, ambiguous={**current.ambiguous, **added.ambiguous},
                   by_model=True)


def resolve(current: HeaderMap, target: str, answer: str) -> HeaderMap | None:
    """The user's pick for an ambiguous field, or None when the answer names none of its candidates."""
    candidates = current.ambiguous.get(target, ())
    picked = next((i for i in candidates if current.header[i].strip().casefold() == (answer or "").strip().casefold()),
                  None)
    if picked is None:
        return None
    rest = {f: v for f, v in current.ambiguous.items() if f != target}
    return replace(current, fields={**current.fields, target: picked}, ambiguous=rest)


def with_name_fallback(current: HeaderMap) -> HeaderMap:
    """Without a name column, the first column not mapped to anything names the dashboards."""
    if "name" in current.fields or "name" in current.ambiguous or not current.unmapped:
        return current
    return replace(current, fields={**current.fields, "name": current.unmapped[0]})


def label(target: str) -> str:
    return _LABEL.get(target, target)


def _settle(header: tuple[str, ...], claims: Mapping[str, Sequence[int]]) -> HeaderMap:
    fields = {f: idx[0] for f, idx in claims.items() if len(idx) == 1}
    ambiguous = {f: tuple(idx) for f, idx in claims.items() if len(idx) > 1}
    return HeaderMap(header, fields, ambiguous)
