"""Pure data: a chat thread as read this turn, a verified finding (a concluded thread with an untracked
action), a proposed issue and the tracker context. Findings, drafts and the tracker context have a JSON-safe
dict form: they live in the checkpointer and on the A2A task between turns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from textcheck import parse_day


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
            values[f.name] = tuple(str(x) for x in raw or ())
        else:
            values[f.name] = text_of(raw) if raw is not None else ""
    return cls(**values)


@dataclass(frozen=True)
class ChatMessage:
    """One message of a thread; every field came from a tool (or the paste) this turn."""

    author: str
    text: str
    ts: str = ""
    bot: bool = False


@dataclass(frozen=True)
class ThreadCandidate:
    """A thread read this turn, its messages in time order (code sorted them)."""

    id: str
    channel: str = ""
    channel_id: str = ""
    thread_ts: str = ""
    link: str = ""
    messages: tuple[ChatMessage, ...] = ()
    pasted: bool = False

    @property
    def participants(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(m.author for m in self.messages if not m.bot and m.author))

    @property
    def last_ts(self) -> str:
        return self.messages[-1].ts if self.messages else ""

    @property
    def date(self) -> str:
        day = parse_day(self.last_ts)
        return day.isoformat() if day else ""

    @property
    def text(self) -> str:
        return "\n".join(f"{m.author}: {m.text}" for m in self.messages)


@dataclass(frozen=True)
class Finding:
    """A concluded thread whose action quote was found verbatim in this turn's input."""

    id: str
    channel: str
    action: str
    action_quote: str
    kind: str
    title: str
    channel_id: str = ""
    thread_ts: str = ""
    link: str = ""
    date: str = ""
    last_ts: str = ""
    text: str = ""
    participants: tuple[str, ...] = ()
    owner_name: str | None = None    # shown as text only, never set in the tracker
    owner_quote: str | None = None
    due: str | None = None
    tracked_key: str | None = None   # an existing issue that already tracks the action
    tracked_url: str | None = None
    tracked_by: str | None = None    # "tracker" (found by search)
    pasted: bool = False

    @property
    def can_link_back(self) -> bool:
        return bool(self.channel_id and self.thread_ts) and not self.pasted

    def to_dict(self) -> dict:
        return {**asdict(self), "participants": list(self.participants)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Finding:
        return _load(Finding, data)


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
    """The exact issue a create card shows and the guard binds. Priority is never set by this agent."""

    finding_id: str
    title: str
    description: str
    team: Named | None = None
    project: Named | None = None
    labels: tuple[Named, ...] = ()
    priority: str | None = None
    due: str | None = None

    def to_dict(self) -> dict:
        return {"finding_id": self.finding_id, "title": self.title, "description": self.description,
                "team": asdict(self.team) if self.team else None,
                "project": asdict(self.project) if self.project else None,
                "labels": [asdict(x) for x in self.labels], "priority": self.priority, "due": self.due}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> IssueDraft:
        return IssueDraft(
            finding_id=str(data.get("finding_id") or ""), title=str(data.get("title") or ""),
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
    dup_check_ok: bool = True
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict:
        return {"family": self.family, "teams": [asdict(t) for t in self.teams],
                "projects": [asdict(p) for p in self.projects], "labels": [asdict(x) for x in self.labels],
                "dup_check_ok": self.dup_check_ok, "notes": list(self.notes)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> TrackerContext:
        data = data or {}
        return TrackerContext(
            family=str(data.get("family") or ""), teams=tuple(Named.from_dict(t) for t in data.get("teams") or ()),
            projects=tuple(Named.from_dict(p) for p in data.get("projects") or ()),
            labels=tuple(Named.from_dict(x) for x in data.get("labels") or ()),
            dup_check_ok=bool(data.get("dup_check_ok", True)), notes=tuple(str(n) for n in data.get("notes") or ()))
