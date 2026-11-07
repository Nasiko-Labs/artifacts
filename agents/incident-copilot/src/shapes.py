"""Pure: read data from raw tool output by its shape, so numbers never pass through the model.

- metric series: lists of `[ts, value]` pairs, or lists of `{timestamp|time|ts, value}` objects
- log events: objects with a timestamp field plus `message`, `content` or `msg`
- error issues: objects with an id, a title and a first-seen time, with stack-frame files and count buckets
- changed files: objects with `filename`/`path` plus `patch`/`status`/`additions`
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from fence import RawCall
from jsonutil import first_key, parse_json, walk_dicts
from records import Issue, LogLine, Series
from windows import Window, iso, parse_time

TS_KEYS = ("timestamp", "time", "ts", "@timestamp", "date", "datetime")
MSG_KEYS = ("message", "content", "msg")
LEVEL_KEYS = ("level", "status", "severity", "log_level", "loglevel")
ERROR_LEVELS = frozenset({"error", "err", "critical", "crit", "fatal", "emergency", "emerg", "alert", "panic"})
ERROR_TEXT = re.compile(r"\b(error|exception|fatal|panic|timeout)\b", re.IGNORECASE)
LABEL_KEYS = ("query", "q", "expression", "expr", "metric", "metric_name", "queries", "search")
SERIES_NAME_KEYS = ("metric", "expression", "display_name", "name", "scope", "label")
FIRST_SEEN_KEYS = ("first_seen", "firstseen", "first_seen_at", "firstSeen")
LAST_SEEN_KEYS = ("last_seen", "lastseen", "last_seen_at", "lastSeen")
COUNT_KEYS = ("count", "times_seen", "timesseen", "event_count", "events")
LINK_KEYS = ("permalink", "web_url", "html_url", "url", "link")
FRAME_FILE_KEYS = ("filename", "abs_path", "file")
FRAME_HINT_KEYS = ("lineno", "line", "function", "in_app", "colno")
FILE_KEYS = ("filename", "path")
FILE_HINT_KEYS = ("patch", "status", "additions", "deletions", "changes")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _label(arguments: Mapping[str, Any]) -> str:
    value = first_key(dict(arguments), LABEL_KEYS)
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    if value is None:
        value = next((v for v in arguments.values() if isinstance(v, str) and v.strip()), "series")
    return str(value)[:160]


def _pairs(items: list) -> list[tuple[str, float]] | None:
    """`[[ts, v], ...]` or `[{ts, value}, ...]` as (ISO time, value); None when the list is not a series."""
    points: list[tuple[str, float]] = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            moment, value = parse_time(item[0]), _number(item[1])
        elif isinstance(item, dict):
            moment, value = parse_time(first_key(item, TS_KEYS)), _number(first_key(item, ("value", "v", "y")))
        else:
            return None
        if moment is None:
            return None
        if value is not None:
            points.append((iso(moment), value))
    return points if items else None


def _series_in(value: Any, label: str, depth: int = 0) -> Iterable[Series]:
    if depth > 30:
        return
    if isinstance(value, dict):
        name = first_key(value, SERIES_NAME_KEYS)
        here = f"{label} [{name}]" if isinstance(name, str) and name and name not in label else label
        for child in value.values():
            if isinstance(child, list):
                points = _pairs(child)
                if points:
                    yield Series(here, tuple(points))
                    continue
            yield from _series_in(child, here, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _series_in(child, label, depth + 1)


def read_series(calls: Sequence[RawCall], window: Window) -> tuple[Series, ...]:
    found: list[Series] = []
    for call in calls:
        for series in _series_in(parse_json(call.text), _label(call.arguments)):
            kept = tuple(p for p in series.points if window.contains(parse_time(p[0])))
            if kept:
                found.append(Series(series.label, kept))
    return tuple(found)


def is_error_level(level: str, message: str) -> bool:
    return level.casefold() in ERROR_LEVELS or bool(ERROR_TEXT.search(message))


def read_logs(calls: Sequence[RawCall], window: Window) -> tuple[LogLine, ...]:
    lines: list[LogLine] = []
    for call in calls:
        for record in walk_dicts(parse_json(call.text)):
            message = first_key(record, MSG_KEYS)
            moment = parse_time(first_key(record, TS_KEYS))
            if not isinstance(message, str) or moment is None or not window.contains(moment):
                continue
            level = first_key(record, LEVEL_KEYS)
            level_text = level if isinstance(level, str) else ""
            if is_error_level(level_text, message):
                lines.append(LogLine(iso(moment), message, level_text))
    return tuple(dict.fromkeys(lines))


def _frames(value: Any) -> tuple[str, ...]:
    files = []
    for record in walk_dicts(value):
        path = first_key(record, FRAME_FILE_KEYS)
        if isinstance(path, str) and any(k in record for k in FRAME_HINT_KEYS):
            files.append(path)
    return tuple(dict.fromkeys(files))


def _buckets(record: dict) -> tuple[tuple[str, int], ...]:
    stats = record.get("stats")
    if not isinstance(stats, (dict, list)):
        return ()
    for series in _series_in({"stats": stats}, "stats"):
        return tuple((at, int(n)) for at, n in series.points)
    return ()


def _arg_values(arguments: Mapping[str, Any]) -> frozenset[str]:
    out: set[str] = set()
    for value in arguments.values():
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            out.add(str(value))
        elif isinstance(value, list):
            out.update(str(v) for v in value if isinstance(v, (str, int)))
    return frozenset(out)


def read_issues(calls: Sequence[RawCall]) -> tuple[Issue, ...]:
    """Issues by shape. Frames and buckets from another call are attached when that call named the issue id."""
    issues: dict[str, Issue] = {}
    for call in calls:
        for record in walk_dicts(parse_json(call.text)):
            ident, title = first_key(record, ("id", "issue_id", "short_id")), first_key(record, ("title",))
            first = parse_time(first_key(record, FIRST_SEEN_KEYS))
            if ident is None or not isinstance(title, str) or first is None:
                continue
            last = parse_time(first_key(record, LAST_SEEN_KEYS))
            count = _number(first_key(record, COUNT_KEYS))
            link = first_key(record, LINK_KEYS)
            issues.setdefault(str(ident), Issue(
                id=str(ident), title=title, first_seen=iso(first), last_seen=iso(last) if last else "",
                count=int(count) if count is not None else None, files=_frames(record),
                link=link if isinstance(link, str) else "", buckets=_buckets(record)))
    return tuple(_enriched(issue, calls) for issue in issues.values())


def _enriched(issue: Issue, calls: Sequence[RawCall]) -> Issue:
    files, buckets = list(issue.files), issue.buckets
    for call in calls:
        if issue.id not in _arg_values(call.arguments):
            continue
        body = parse_json(call.text)
        files.extend(_frames(body))
        if not buckets:
            buckets = next((b for r in walk_dicts(body) if (b := _buckets(r))), ())
    return replace(issue, files=tuple(dict.fromkeys(files)), buckets=buckets)


def read_changed_files(calls: Sequence[RawCall]) -> tuple[tuple[frozenset[str], tuple[str, ...]], ...]:
    """Per call: the argument values it was called with (a sha, a PR number) and the changed files it returned."""
    out = []
    for call in calls:
        files = []
        for record in walk_dicts(parse_json(call.text)):
            path = first_key(record, FILE_KEYS)
            if isinstance(path, str) and any(k in record for k in FILE_HINT_KEYS):
                files.append(path)
        if files:
            out.append((_arg_values(call.arguments), tuple(dict.fromkeys(files))))
    return tuple(out)
