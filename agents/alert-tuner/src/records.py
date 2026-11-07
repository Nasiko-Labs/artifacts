"""Pure: read incidents, log entries, notes, files and repository facts out of raw tool output by their
shape (the field names an incident pager or a code host returns), never from the model's words.

Every value here came out of a tool result this turn, so ids, numbers and links are verbatim by
construction.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_RESOLVED = frozenset({"resolved", "closed", "ok", "recovered"})


@dataclass(frozen=True)
class Incident:
    id: str
    number: str
    title: str
    service: str
    status: str
    created: str
    resolved: str = ""
    url: str = ""
    resolved_by: str = ""       # "human" | "system" | "" (unknown) when the incident itself says
    acked: bool = False          # the incident itself carries an acknowledgement

    @property
    def is_resolved(self) -> bool:
        return self.status.casefold() in _RESOLVED

    @property
    def label(self) -> str:
        return f"#{self.number}" if self.number else self.id


@dataclass(frozen=True)
class LogEntry:
    incident_id: str
    kind: str                    # acknowledge | annotate | escalate | reassign | resolve | other
    actor: str                   # "human" | "system" | ""


@dataclass(frozen=True)
class RepoFile:
    path: str
    text: str
    sha: str = ""


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def walk(value: Any) -> Iterator[Mapping[str, Any]]:
    """Every mapping in a JSON value, depth first; a hosted connector may wrap the API response."""
    if isinstance(value, Mapping):
        yield value
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def _s(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _name(value: Any) -> str:
    if isinstance(value, Mapping):
        return _s(value.get("summary") or value.get("name") or value.get("id"))
    return _s(value)


def _actor(ref: Any) -> str:
    kind = _s(ref.get("type")).casefold() if isinstance(ref, Mapping) else _s(ref).casefold()
    if not kind:
        return ""
    return "human" if "user" in kind else "system"


def incidents(text: str) -> list[Incident]:
    found = []
    for obj in walk(parse_json(text)):
        incident = _incident(obj)
        if incident is not None:
            found.append(incident)
    return found


def _incident(obj: Mapping[str, Any]) -> Incident | None:
    title = _s(obj.get("title") or obj.get("summary") or obj.get("description"))
    created = _s(obj.get("created_at") or obj.get("created_on") or obj.get("created"))
    status = _s(obj.get("status") or obj.get("state"))
    ident = _s(obj.get("id"))
    if not (title and created and status and ident) or "_log_entry" in _s(obj.get("type")):
        return None
    if not isinstance(obj.get("service"), (Mapping, str)) and "incident_number" not in obj:
        return None
    resolved = _s(obj.get("resolved_at") or obj.get("resolved_on"))
    if not resolved and status.casefold() in _RESOLVED:
        resolved = _s(obj.get("last_status_change_at"))
    by = _actor(obj.get("last_status_change_by")) if status.casefold() in _RESOLVED else ""
    return Incident(id=ident, number=_s(obj.get("incident_number") or obj.get("number")), title=title,
                    service=_name(obj.get("service")), status=status, created=created, resolved=resolved,
                    url=_s(obj.get("html_url") or obj.get("url")), resolved_by=by,
                    acked=bool(obj.get("acknowledgements")))


def has_incident_list(text: str) -> bool:
    """The result is an incident listing (an `incidents` list, possibly empty, or incident objects)."""
    return any(isinstance(obj.get("incidents"), list) for obj in walk(parse_json(text))) or bool(incidents(text))


def log_entries(text: str, fallback_incident: str = "") -> list[LogEntry]:
    entries = []
    for obj in walk(parse_json(text)):
        kind = _s(obj.get("type") or obj.get("event_type") or obj.get("action")).casefold()
        if "log_entry" not in kind and not ("incident" in obj and "agent" in obj):
            continue
        ref = obj.get("incident")
        incident = _s(ref.get("id")) if isinstance(ref, Mapping) else _s(ref)
        entries.append(LogEntry(incident or fallback_incident, _entry_kind(kind), _actor(obj.get("agent"))))
    return entries


def _entry_kind(kind: str) -> str:
    for word, name in (("acknowledge", "acknowledge"), ("annotate", "annotate"), ("escalat", "escalate"),
                       ("reassign", "reassign"), ("assign", "reassign"), ("resolve", "resolve")):
        if word in kind:
            return name
    return "other"


def has_log_list(text: str) -> bool:
    """The result is a (possibly empty) list of log entries, so the log was read."""
    return any("log_entries" in obj for obj in walk(parse_json(text))) or bool(log_entries(text))


def notes(text: str) -> int:
    """How many notes the result holds: objects with `content` and an author/time, outside log entries."""
    count = 0
    for obj in walk(parse_json(text)):
        if "content" in obj and ("user" in obj or "created_at" in obj) and "_log_entry" not in _s(obj.get("type")):
            count += 1
    return count


def is_notes_list(text: str) -> bool:
    return any("notes" in obj for obj in walk(parse_json(text)))


def ids_in(arguments: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for value in arguments.values():
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            out.append(str(value))
        elif isinstance(value, list):
            out.extend(str(v) for v in value if isinstance(v, (str, int)))
    return tuple(out)


def files(text: str) -> list[RepoFile]:
    out = []
    for obj in walk(parse_json(text)):
        path, content = _s(obj.get("path")), obj.get("content")
        if not path or not isinstance(content, str):
            continue
        body = _decode(content) if _s(obj.get("encoding")).casefold() == "base64" else content
        if body is not None:
            out.append(RepoFile(path, body, _s(obj.get("sha"))))
    return out


def _decode(content: str) -> str | None:
    try:
        return base64.b64decode(content.replace("\n", ""), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def search_paths(text: str) -> list[str]:
    """Paths of code-search hits: objects with `path` and a repository or a `name`, but no content."""
    return [_s(o["path"]) for o in walk(parse_json(text))
            if _s(o.get("path")) and "content" not in o and ("repository" in o or "name" in o or "html_url" in o)]


def default_branch(text: str) -> str:
    return next((_s(o["default_branch"]) for o in walk(parse_json(text)) if _s(o.get("default_branch"))), "")


def branch_sha(text: str, branch: str) -> str:
    """The head commit of `branch`: a branch object `{name, commit: {sha}}` or a ref `{ref, object: {sha}}`."""
    for obj in walk(parse_json(text)):
        name = _s(obj.get("name")) or _s(obj.get("ref")).removeprefix("refs/heads/")
        target = obj.get("commit") if isinstance(obj.get("commit"), Mapping) else obj.get("object")
        if name == branch and isinstance(target, Mapping) and _s(target.get("sha")):
            return _s(target["sha"])
    return ""


def link(text: str, *keys: str) -> str:
    for obj in walk(parse_json(text)):
        for key in keys:
            value = _s(obj.get(key))
            if value.startswith("http"):
                return value
    return ""


def parse_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(int(text[:10]), UTC)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
