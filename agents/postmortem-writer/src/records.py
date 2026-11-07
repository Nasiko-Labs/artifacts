"""The postmortem's records and their plain-dict form (stored in the checkpointer). All frozen: an edit
("owner of a2 is Priya") builds a new record with `dataclasses.replace`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from typing import Any

CATEGORIES = ("trigger", "impact", "detection", "escalation", "diagnosis", "mitigation", "resolution",
              "communication")
FACTOR_CLASSES = ("trigger", "latent_condition", "detection_gap", "response_gap", "process_gap", "tooling_gap")
ACTION_TYPES = ("prevent", "detect", "mitigate", "process")
TBD = "TBD"
NOT_STATED = "Not stated"
METRIC_KEYS = ("time_to_detect", "time_to_mitigate", "duration")


@dataclass(frozen=True)
class Event:
    id: str
    category: str
    text: str
    snippet: str
    time_text: str = ""
    time_utc: str = ""
    tz: str = ""
    stamp: str = ""        # ISO, as parsed in code ("" when untimed)
    speaker: str = ""
    line: int = 0
    collapsed: int = 0     # consecutive same-category rows folded into this one


@dataclass(frozen=True)
class Factor:
    id: str
    cls: str
    text: str
    snippet: str
    inferred: bool = False
    event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Action:
    id: str
    title: str
    owner: str = TBD
    due: str = TBD
    type: str = "process"
    source: str = "log"            # log | suggested | you
    factor_ids: tuple[str, ...] = ()
    snippet: str = ""
    owner_set_by_you: bool = False


@dataclass(frozen=True)
class Postmortem:
    title: str
    severity: str = ""
    date: str = ""
    events: tuple[Event, ...] = ()
    factors: tuple[Factor, ...] = ()
    actions: tuple[Action, ...] = ()
    metrics: Mapping[str, str] = field(default_factory=dict)
    tz_label: str = ""
    summary: str = ""
    impact: str = ""
    went_well: tuple[str, ...] = ()
    went_poorly: tuple[str, ...] = ()
    lucky: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    dropped: int = 0
    collapsed: int = 0
    notes: tuple[str, ...] = ()
    confidence: str = "high"
    needs_human: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        data["metrics"] = dict(self.metrics)
        return data

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Postmortem:
        data = dict(data or {})
        known = {f.name for f in fields(Postmortem)}
        values = {k: v for k, v in data.items() if k in known}
        values["events"] = tuple(_record(Event, e) for e in data.get("events") or ())
        values["factors"] = tuple(_record(Factor, f) for f in data.get("factors") or ())
        values["actions"] = tuple(_record(Action, a) for a in data.get("actions") or ())
        for key in ("went_well", "went_poorly", "lucky", "questions", "notes"):
            values[key] = tuple(str(x) for x in data.get(key) or ())
        values["metrics"] = {str(k): str(v) for k, v in (data.get("metrics") or {}).items()}
        for key in ("dropped", "collapsed"):
            values[key] = int(data.get(key) or 0)
        return Postmortem(**values)


def _record(cls, data: Mapping[str, Any]):
    names = {f.name: f for f in fields(cls)}
    values = {}
    for key, value in dict(data).items():
        if key not in names:
            continue
        values[key] = tuple(str(v) for v in value) if isinstance(value, (list, tuple)) else value
    for key in ("line", "collapsed"):
        if key in values:
            values[key] = int(values[key] or 0)
    return cls(**values)


def action_dict(action: Action) -> dict:
    """The catalog's action_items[] shape."""
    return {"id": action.id, "title": action.title, "owner": action.owner, "due": action.due, "type": action.type,
            "source": action.source, "factor_ids": list(action.factor_ids), "snippet": action.snippet}


def as_dicts(records: Sequence[Any]) -> list[dict]:
    out = []
    for record in records:
        data = asdict(record)
        out.append({k: list(v) if isinstance(v, tuple) else v for k, v in data.items()})
    return out
