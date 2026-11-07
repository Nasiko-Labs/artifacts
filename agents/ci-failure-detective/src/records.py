"""Pure: read runs, jobs, pull requests, commits, files, annotations and issues out of raw tool output
by their shape (field names a CI or code-host API returns), plus the verbatim checks.

Code reads the facts itself instead of trusting the model to copy ids: every value here came out of
a tool result this turn. The model's own JSON is only a fallback, and each id it names must appear
verbatim in this turn's raw tool output (`seen`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from culprit import ChangedFile, Commit

_LOG_MIN_LINES = 5
_ARCHIVE = re.compile(r"https?://\S+(?:\.zip|/logs?\b|logs?\?|/archive)\S*", re.IGNORECASE)


@dataclass(frozen=True)
class Job:
    job_id: str
    run_id: str
    name: str
    conclusion: str
    started_at: str = ""
    failed_step: str = ""
    url: str = ""


@dataclass(frozen=True)
class Run:
    run_id: str
    workflow: str
    workflow_id: str
    head_sha: str
    branch: str
    created_at: str
    conclusion: str
    status: str = ""
    url: str = ""
    attempt: str = "1"
    event: str = ""
    pr_numbers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PullRequest:
    number: str
    head_sha: str
    head_branch: str
    base_branch: str
    title: str = ""
    url: str = ""


@dataclass(frozen=True)
class Annotation:
    path: str
    line: int
    level: str
    message: str

    def as_log_line(self) -> str:
        return f"{self.path}:{self.line}: {self.level}: {self.message}"


@dataclass(frozen=True)
class Issue:
    number: str
    title: str
    state: str
    url: str = ""
    body: str = ""


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def objects(text: str) -> Iterator[Mapping[str, Any]]:
    """Every JSON object at any depth of a tool output (JSON strings that hold JSON are opened too)."""
    yield from _walk(parse_json(text), 0)


def _walk(value: Any, depth: int) -> Iterator[Mapping[str, Any]]:
    if depth > 12:
        return
    if isinstance(value, Mapping):
        yield value
        for v in value.values():
            yield from _walk(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, depth + 1)
    elif isinstance(value, str) and value[:1] in "[{" and len(value) < 2_000_000:
        yield from _walk(parse_json(value), depth + 1)


def _s(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def runs(text: str) -> list[Run]:
    out = []
    for o in objects(text):
        if "id" in o and "head_sha" in o and "run_id" not in o and ("workflow_id" in o or "run_number" in o or "run_attempt" in o):
            prs = tuple(_s(p.get("number")) for p in o.get("pull_requests") or () if isinstance(p, Mapping))
            out.append(Run(_s(o["id"]), _s(o.get("name") or o.get("display_title")), _s(o.get("workflow_id")),
                           _s(o["head_sha"]), _s(o.get("head_branch")), _s(o.get("created_at") or o.get("run_started_at")),
                           _s(o.get("conclusion")), _s(o.get("status")), _s(o.get("html_url")),
                           _s(o.get("run_attempt") or 1), _s(o.get("event")), prs))
    return out


def jobs(text: str) -> list[Job]:
    out = []
    for o in objects(text):
        if "id" in o and "run_id" in o and ("steps" in o or "conclusion" in o) and "head_sha" in o:
            steps = [s for s in o.get("steps") or () if isinstance(s, Mapping)]
            failed = next((_s(s.get("name")) for s in steps if s.get("conclusion") in ("failure", "timed_out")), "")
            out.append(Job(_s(o["id"]), _s(o["run_id"]), _s(o.get("name")), _s(o.get("conclusion")),
                           _s(o.get("started_at")), failed, _s(o.get("html_url"))))
    return out


def pull_requests(text: str) -> list[PullRequest]:
    out = []
    for o in objects(text):
        head, base = o.get("head"), o.get("base")
        if "number" in o and isinstance(head, Mapping) and isinstance(base, Mapping) and head.get("sha"):
            out.append(PullRequest(_s(o["number"]), _s(head.get("sha")), _s(head.get("ref")), _s(base.get("ref")),
                                   _s(o.get("title")), _s(o.get("html_url"))))
    return out


def commits(text: str) -> list[Commit]:
    out = []
    for o in objects(text):
        inner = o.get("commit")
        if not (isinstance(o.get("sha"), str) and isinstance(inner, Mapping) and "message" in inner):
            continue
        author = inner.get("author") if isinstance(inner.get("author"), Mapping) else {}
        login = o.get("author") if isinstance(o.get("author"), Mapping) else {}
        raw_files = o.get("files")
        files = (tuple(ChangedFile(_s(f.get("filename")), _s(f.get("patch"))) for f in raw_files
                       if isinstance(f, Mapping) and f.get("filename")) if isinstance(raw_files, list) else None)
        out.append(Commit(o["sha"], _s(login.get("login")), _s(author.get("name")), _s(author.get("date")),
                          _s(inner.get("message")).splitlines()[0] if inner.get("message") else "",
                          _s(o.get("html_url")), files))
    return out


def annotations(text: str) -> list[Annotation]:
    out = []
    for o in objects(text):
        if "annotation_level" in o and "message" in o:
            line = o.get("start_line") or o.get("line") or 0
            out.append(Annotation(_s(o.get("path")), int(line) if _s(line).isdigit() else 0,
                                  _s(o.get("annotation_level")), _s(o.get("message"))))
    return out


def issues(text: str) -> list[Issue]:
    out = []
    for o in objects(text):
        if "number" in o and "title" in o and "state" in o and "head" not in o:
            out.append(Issue(_s(o["number"]), _s(o["title"]), _s(o["state"]), _s(o.get("html_url")), _s(o.get("body"))))
    return out


def log_text(text: str) -> str:
    """The job log in a tool output: the output itself when it is plain text, else its longest
    multi-line string field. Empty when the output holds no log."""
    data = parse_json(text)
    if data is None:
        return text if text.count("\n") + 1 >= _LOG_MIN_LINES else ""
    best = max(_strings(data), key=len, default="")
    return best if best.count("\n") + 1 >= _LOG_MIN_LINES else ""


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def archive_url(text: str) -> str:
    """A download link in place of log text (logs delivered as an archive), or ""."""
    m = _ARCHIVE.search(text or "")
    return m.group(0).rstrip('",}') if m else ""


def parse_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def verbatim(value: str, seen: str) -> bool:
    """True when the value appears exactly in this turn's raw tool output."""
    return bool(value) and value in seen


def mentioned_ids(arguments: Mapping[str, Any]) -> frozenset[str]:
    """Every scalar argument value as text: which run/job a tool call was about."""
    found: set[str] = set()
    for v in arguments.values():
        if isinstance(v, (str, int)) and not isinstance(v, bool):
            found.add(str(v))
        elif isinstance(v, list):
            found.update(str(x) for x in v if isinstance(x, (str, int)))
    return frozenset(found)
