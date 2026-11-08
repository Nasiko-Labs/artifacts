"""Pure: turn a step's JSON answer into checked records, and pick the sprint.

The model only transcribes. A record is kept only when its key and URL appear in THIS step's tool
output, its timestamps parse and appear there too (ISO text or epoch seconds), and it is not a
duplicate. History events must belong to a listed issue and fall in [sprint start - 1 day, now].
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from records import EVENT_KINDS, HistoryEvent, Sprint, SprintIssue, parse_time

MAX_ISSUES = 200
MAX_TEAMS = 10
EVENT_LOOKBACK = timedelta(days=1)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class Seen:
    """This step's raw tool output, the only source a record may come from."""

    text: str

    def has(self, value: object) -> bool:
        return isinstance(value, str) and bool(value.strip()) and value.strip() in self.text

    def has_number(self, value: float) -> bool:
        """The number as written in the output (not part of a longer number)."""
        text = str(int(value)) if float(value).is_integer() else repr(float(value))
        return re.search(rf"(?<![\d.]){re.escape(text)}(?![\d])", self.text) is not None

    def has_time(self, raw: object) -> datetime | None:
        """The timestamp, when the output contains it as written or as epoch seconds/milliseconds."""
        at = parse_time(raw)
        if at is None:
            return None
        if isinstance(raw, str) and self.has(raw):
            return at
        seconds = int(at.timestamp())
        return at if str(seconds) in self.text or str(seconds * 1000) in self.text else None


def parse_answer(text: str) -> dict | None:
    """The JSON object in a model answer (code fences and surrounding prose tolerated), or None."""
    body = _FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def items(answer: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = answer.get(key)
    return [i for i in raw if isinstance(i, Mapping)] if isinstance(raw, list) else []


def check_teams(answer: Mapping[str, Any], seen: Seen) -> tuple[str, ...]:
    names = [str(t.get("name")).strip() for t in items(answer, "teams") if seen.has(t.get("name"))]
    return tuple(dict.fromkeys(names))


def check_sprints(answer: Mapping[str, Any], seen: Seen) -> tuple[Sprint, ...]:
    raw = items(answer, "sprints") or ([answer["sprint"]] if isinstance(answer.get("sprint"), Mapping) else [])
    kept: dict[str, Sprint] = {}
    for s in raw:
        sid, name = s.get("id"), s.get("name")
        starts, ends = seen.has_time(s.get("starts_at")), seen.has_time(s.get("ends_at"))
        if not (seen.has(sid) or seen.has(name)) or starts is None or ends is None or ends <= starts:
            continue
        ident = str(sid or name).strip()
        kept.setdefault(ident, Sprint(id=ident, name=_str(name, seen) or ident, starts_at=starts, ends_at=ends,
                                      team=_str(s.get("team"), seen), url=_str(s.get("url"), seen)))
    return tuple(kept.values())


def select_sprint(sprints: Sequence[Sprint], selector: str, team: str | None,
                  now: datetime) -> tuple[Sprint | None, tuple[str, ...]]:
    """(the sprint, other names that also matched). `selector` is current | last | a number."""
    pool = [s for s in sprints if not team or not s.team or team.casefold() in s.team.casefold()] or list(sprints)
    if selector == "current":
        matches = [s for s in pool if s.starts_at <= now < s.ends_at]
    elif selector == "last":
        ended = sorted((s for s in pool if s.ends_at <= now), key=lambda s: s.ends_at)
        matches = ended[-1:]
    else:
        number = re.compile(rf"(?<!\d){re.escape(selector)}(?!\d)")
        matches = [s for s in pool if number.search(s.name) or s.id == selector]
    if not matches:
        return None, ()
    ordered = sorted(matches, key=lambda s: s.starts_at, reverse=True)
    return ordered[0], tuple(s.name for s in ordered[1:])


def check_issues(answer: Mapping[str, Any], seen: Seen) -> tuple[tuple[SprintIssue, ...], int]:
    """(kept issues, how many were over the cap)."""
    kept: dict[str, SprintIssue] = {}
    for raw in items(answer, "issues"):
        issue = _issue(raw, seen)
        if issue is not None:
            kept.setdefault(issue.key, issue)
    issues = tuple(kept.values())
    return issues[:MAX_ISSUES], max(len(issues) - MAX_ISSUES, 0)


def _issue(raw: Mapping[str, Any], seen: Seen) -> SprintIssue | None:
    key, url = raw.get("key"), raw.get("url")
    if not seen.has(key) or not seen.has(url):
        return None
    in_now = raw.get("in_sprint_now")
    return SprintIssue(key=key.strip(), url=url.strip(), title=_str(raw.get("title"), seen),
                       estimate=_estimate(raw.get("estimate"), seen), assignee=_str(raw.get("assignee"), seen),
                       status=_str(raw.get("status"), seen), created_at=seen.has_time(raw.get("created_at")),
                       creator=_str(raw.get("creator"), seen), in_sprint_now=in_now is not False,
                       parent=_str(raw.get("parent"), seen))


def check_events(answer: Mapping[str, Any], seen: Seen, keys: frozenset[str], starts_at: datetime,
                 now: datetime) -> tuple[HistoryEvent, ...]:
    kept: dict[tuple, HistoryEvent] = {}
    for raw in items(answer, "events"):
        key, kind = raw.get("issue_key"), raw.get("kind")
        at = seen.has_time(raw.get("at"))
        if key not in keys or kind not in EVENT_KINDS or at is None or not starts_at - EVENT_LOOKBACK <= at <= now:
            continue
        event = HistoryEvent(issue_key=key, at=at, actor=_str(raw.get("actor"), seen), kind=kind,
                             estimate_from=_estimate(raw.get("estimate_from"), seen),
                             estimate_to=_estimate(raw.get("estimate_to"), seen))
        if kind == "estimate_changed" and event.estimate_from is None and event.estimate_to is None:
            continue
        kept.setdefault((key, kind, at), event)
    return tuple(kept.values())


def _estimate(value: object, seen: Seen) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value) if seen.has_number(value) else None


def _str(value: object, seen: Seen) -> str:
    return value.strip() if seen.has(value) else ""
