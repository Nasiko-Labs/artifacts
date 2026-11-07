"""Pure data: an inbox thread, a verified work request, a proposed issue and the tracker context, each
with a JSON-safe dict form (they live in the checkpointer and on the A2A task between turns)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from typing import Any


def text_of(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _load(cls, data: Mapping[str, Any] | None):
    data = data or {}
    values = {}
    for f in fields(cls):
        raw = data.get(f.name)
        if f.type in ("bool",):
            values[f.name] = bool(raw)
        elif f.type in ("str | None",):
            values[f.name] = text_of(raw) or None
        elif f.type.startswith("tuple"):
            values[f.name] = tuple(raw or ())
        else:
            values[f.name] = text_of(raw) if raw is not None else ""
    return cls(**values)


@dataclass(frozen=True)
class Thread:
    """One inbox thread as read this turn (or pasted); only code-checked fields are kept."""

    id: str
    thread_id: str = ""
    message_id: str = ""
    sender: str = ""
    sender_name: str = ""
    subject: str = ""
    date: str = ""
    link: str = ""
    text: str = ""
    user_reply: str = ""
    bulk: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Thread:
        return _load(Thread, data)


@dataclass(frozen=True)
class Request:
    """A work request whose ask quote was found verbatim in this turn's input."""

    id: str
    sender: str
    sender_name: str
    subject: str
    ask_quote: str
    ask_type: str
    title: str
    date: str = ""
    link: str = ""
    message_id: str = ""
    thread_id: str = ""
    text: str = ""
    deadline_phrase: str | None = None
    due: str | None = None
    priority: str | None = None
    tracked_key: str | None = None   # an existing issue key or link
    tracked_by: str | None = None    # "reply" (the user already replied with it) | "tracker" (found by search)
    tracked_url: str | None = None
    pasted: bool = False

    @property
    def who(self) -> str:
        return self.sender_name or self.sender or "the sender"

    @property
    def source_ref(self) -> str:
        return self.link or self.message_id

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Request:
        return _load(Request, data)


@dataclass(frozen=True)
class Named:
    """A team, project or label as the tracker returned it."""

    id: str
    name: str

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Named:
        data = data or {}
        return Named(id=str(data.get("id") or ""), name=str(data.get("name") or ""))


@dataclass(frozen=True)
class IssueDraft:
    """The exact issue a create card shows and the guard binds."""

    request_id: str
    title: str
    description: str
    team: Named | None = None
    project: Named | None = None
    labels: tuple[Named, ...] = ()
    priority: str | None = None
    due: str | None = None

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "title": self.title, "description": self.description,
                "team": asdict(self.team) if self.team else None,
                "project": asdict(self.project) if self.project else None,
                "labels": [asdict(x) for x in self.labels], "priority": self.priority, "due": self.due}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> IssueDraft:
        return IssueDraft(
            request_id=str(data.get("request_id") or ""), title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            team=Named.from_dict(data["team"]) if data.get("team") else None,
            project=Named.from_dict(data["project"]) if data.get("project") else None,
            labels=tuple(Named.from_dict(x) for x in data.get("labels") or ()),
            priority=data.get("priority") or None, due=data.get("due") or None)


@dataclass(frozen=True)
class TrackerContext:
    """What the code-driven tracker read found this turn."""

    family: str = ""
    teams: tuple[Named, ...] = ()
    projects: tuple[Named, ...] = ()
    labels: tuple[Named, ...] = ()
    can_create: bool = False
    dup_check_ok: bool = True
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict:
        return {"family": self.family, "teams": [asdict(t) for t in self.teams],
                "projects": [asdict(p) for p in self.projects], "labels": [asdict(x) for x in self.labels],
                "can_create": self.can_create, "dup_check_ok": self.dup_check_ok, "notes": list(self.notes)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> TrackerContext:
        data = data or {}
        return TrackerContext(
            family=str(data.get("family") or ""), teams=tuple(Named.from_dict(t) for t in data.get("teams") or ()),
            projects=tuple(Named.from_dict(p) for p in data.get("projects") or ()),
            labels=tuple(Named.from_dict(x) for x in data.get("labels") or ()),
            can_create=bool(data.get("can_create")), dup_check_ok=bool(data.get("dup_check_ok", True)),
            notes=tuple(str(n) for n in data.get("notes") or ()))
