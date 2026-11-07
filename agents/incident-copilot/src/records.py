"""Pure: the record contracts the model returns, and the code checks that keep only what a tool returned.

Every id, SHA, URL, timestamp and quote a record cites must appear in this loop's raw tool output
(evidence.Haystack). A record that fails a required check is dropped and counted; an optional field that
fails (a chat channel, a runbook link, an author email, a resolution note) is cleared instead.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from typing import Any, TypeVar

from evidence import Haystack
from jsonutil import load_json_object, text_of
from windows import iso, parse_time

T = TypeVar("T")


@dataclass(frozen=True)
class Incident:
    id: str
    title: str
    service: str
    status: str
    created_at: str
    url: str = ""
    chat_ref: str = ""
    runbook_url: str = ""
    repo_url: str = ""


@dataclass(frozen=True)
class Change:
    kind: str            # "deploy" | "merged_pr"
    ref: str
    sha: str
    time: str            # ISO UTC, computed from the verbatim value
    author_login: str
    title: str
    link: str
    author_email: str = ""
    environment: str = ""
    files: tuple[str, ...] = ()
    prs: tuple[str, ...] = ()   # merged PRs this deploy shipped


@dataclass(frozen=True)
class Issue:
    id: str
    title: str
    first_seen: str      # ISO UTC
    last_seen: str = ""
    count: int | None = None
    files: tuple[str, ...] = ()
    link: str = ""
    buckets: tuple[tuple[str, int], ...] = ()   # (ISO time, events), only when read by shape


@dataclass(frozen=True)
class PastIncident:
    id: str
    title: str
    service: str
    created_at: str      # ISO UTC
    url: str = ""
    resolved_at: str = ""
    resolution_quote: str = ""
    postmortem_url: str = ""


@dataclass(frozen=True)
class Doc:
    title: str
    url: str


@dataclass(frozen=True)
class Checked:
    """Records that passed, and how many were dropped."""

    kept: tuple = ()
    dropped: int = 0


@dataclass(frozen=True)
class LogLine:
    at: str
    quote: str
    level: str = ""


@dataclass(frozen=True)
class Series:
    label: str
    points: tuple[tuple[str, float], ...] = field(default=())


def to_dict(record: Any) -> dict:
    return asdict(record)


def from_dict(kind: type[T], data: Mapping[str, Any]) -> T:
    names = {f.name for f in fields(kind)}
    values = {k: v for k, v in dict(data).items() if k in names}
    for key in ("files", "prs"):
        if key in values:
            values[key] = tuple(values[key] or ())
    if "buckets" in values:
        values["buckets"] = tuple((str(a), int(b)) for a, b in values["buckets"] or ())
    if "points" in values:
        values["points"] = tuple((str(a), float(b)) for a, b in values["points"] or ())
    return kind(**values)


def answer_list(text: str, key: str) -> list[dict] | None:
    """The list under `key` in the model's JSON answer; None when the answer is not that JSON."""
    data = load_json_object(text)
    if data is None or not isinstance(data.get(key, []), list):
        return None
    return [item for item in data.get(key) or [] if isinstance(item, dict)]


def answer_status(text: str) -> str:
    data = load_json_object(text)
    return text_of(data.get("status")) if data else ""


def _time(value: Any, seen: Haystack, now: datetime) -> str:
    """ISO UTC of a value that was seen verbatim and parses and is not in the future; else ""."""
    if not seen.has(value):
        return ""
    moment = parse_time(value)
    return iso(moment) if moment is not None and moment <= now else ""


def _optional(value: Any, seen: Haystack) -> str:
    text = text_of(value)
    return text if text and seen.has(text) else ""


def check_all(items: Sequence[dict], check: Callable[[dict], T | None]) -> Checked:
    kept = tuple(r for r in (check(item) for item in items) if r is not None)
    return Checked(kept, len(items) - len(kept))


def check_incident(item: dict, seen: Haystack, now: datetime) -> Incident | None:
    ident, url = text_of(item.get("id")), text_of(item.get("url"))
    created = _time(item.get("created_at"), seen, now)
    if not ident or not seen.has(ident) or not created or (url and not seen.has(url)):
        return None
    return Incident(id=ident, title=text_of(item.get("title")), service=text_of(item.get("service")),
                    status=text_of(item.get("status")), created_at=created, url=url,
                    chat_ref=_optional(item.get("chat_ref"), seen), runbook_url=_optional(item.get("runbook_url"), seen),
                    repo_url=_optional(item.get("repo_url"), seen))


def check_change(item: dict, seen: Haystack, now: datetime) -> Change | None:
    kind = text_of(item.get("kind"))
    ref, sha, login, link = (text_of(item.get(k)) for k in ("ref", "sha", "author_login", "link"))
    moment = _time(item.get("time"), seen, now)
    if kind not in ("deploy", "merged_pr") or not moment or not seen.sha_ok(sha):
        return None
    if not all(seen.has(v) for v in (ref, login, link)):
        return None
    return Change(kind=kind, ref=ref, sha=sha, time=moment, author_login=login, title=text_of(item.get("title")),
                  link=link, author_email=_optional(item.get("author_email"), seen),
                  environment=_optional(item.get("environment"), seen))


def check_issue(item: dict, seen: Haystack, now: datetime) -> Issue | None:
    ident, title, link = (text_of(item.get(k)) for k in ("id", "title", "link"))
    first = _time(item.get("first_seen"), seen, now)
    if not all(seen.has(v) for v in (ident, title, link)) or not first:
        return None
    files = tuple(f for f in (text_of(x) for x in item.get("files") or ()) if f and seen.has(f))
    count = item.get("count")
    return Issue(id=ident, title=title, first_seen=first, last_seen=_time(item.get("last_seen"), seen, now),
                 count=int(count) if isinstance(count, (int, float)) and not isinstance(count, bool) else None,
                 files=files, link=link)


def check_past(item: dict, seen: Haystack, now: datetime) -> PastIncident | None:
    ident, url = text_of(item.get("id")), text_of(item.get("url"))
    created = _time(item.get("created_at"), seen, now)
    if not ident or not seen.has(ident) or not created or (url and not seen.has(url)):
        return None
    return PastIncident(id=ident, title=text_of(item.get("title")), service=text_of(item.get("service")),
                        created_at=created, url=url, resolved_at=_time(item.get("resolved_at"), seen, now),
                        resolution_quote=_optional(item.get("resolution_quote"), seen),
                        postmortem_url=_optional(item.get("postmortem_url"), seen))


def check_doc(item: dict, seen: Haystack) -> Doc | None:
    title, url = text_of(item.get("title")), text_of(item.get("url"))
    return Doc(title, url) if title and url and seen.has(url) else None


def check_line(item: dict, seen: Haystack, now: datetime) -> LogLine | None:
    quote = text_of(item.get("quote"))
    moment = _time(item.get("ts"), seen, now)
    return LogLine(moment, quote) if quote and moment and seen.has(quote) else None


def with_files(change: Change, files: Sequence[str]) -> Change:
    return replace(change, files=tuple(dict.fromkeys((*change.files, *files))))


def with_issue_files(issue: Issue, files: Sequence[str]) -> Issue:
    return replace(issue, files=tuple(dict.fromkeys((*issue.files, *files))))
